mod wire;

use anyhow::{Context, Result, bail, ensure};
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, VecDeque},
    future::pending,
    io::Write,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    sync::{Mutex, mpsc},
};
use wire::{Frame, MAX_CYCLE, cstring};

fn emit(value: impl Serialize) {
    let mut out = std::io::stdout().lock();
    let _ = serde_json::to_writer(&mut out, &value);
    let _ = out.write_all(b"\n");
    let _ = out.flush();
}

struct Shared {
    armed: AtomicBool,
    tripped: AtomicBool,
    counter: AtomicU64,
    // Serialize wire cycles globally while armed. Scope: a sequential workload.
    // Prevent another DB connection from overtaking a selected cut.
    cycle: Mutex<()>,
    cut: u64,
    phase: String,
    include_sql: bool,
}

#[derive(Default, Clone)]
struct Names {
    statements: HashMap<String, String>,
    portals: HashMap<String, String>,
}
impl Names {
    fn observe(&mut self, frame: &Frame) -> Result<Option<String>> {
        let mut p = 0;
        match frame.tag {
            b'Q' => Ok(Some(cstring(&frame.body, &mut p)?)),
            b'P' => {
                let name = cstring(&frame.body, &mut p)?;
                let sql = cstring(&frame.body, &mut p)?;
                ensure!(
                    self.statements.len() < 10_000 || self.statements.contains_key(&name),
                    "prepared statement limit exceeded"
                );
                self.statements.insert(name, sql.clone());
                Ok(Some(sql))
            }
            b'B' => {
                let portal = cstring(&frame.body, &mut p)?;
                let statement = cstring(&frame.body, &mut p)?;
                let sql = self
                    .statements
                    .get(&statement)
                    .context("Bind references unknown statement")?
                    .clone();
                ensure!(
                    self.portals.len() < 10_000 || self.portals.contains_key(&portal),
                    "portal limit exceeded"
                );
                self.portals.insert(portal, sql);
                Ok(None)
            }
            b'E' => {
                let portal = cstring(&frame.body, &mut p)?;
                let sql = self
                    .portals
                    .get(&portal)
                    .context("Execute references unknown portal")?
                    .clone();
                Ok(Some(sql))
            }
            b'C' => {
                ensure!(!frame.body.is_empty(), "empty Close");
                p = 1;
                let name = cstring(&frame.body, &mut p)?;
                match frame.body[0] {
                    b'S' => {
                        self.statements.remove(&name);
                    }
                    b'P' => {
                        self.portals.remove(&name);
                    }
                    _ => bail!("invalid Close type"),
                }
                Ok(None)
            }
            b'D' | b'S' => Ok(None),
            b'H' => bail!(
                "Flush/streaming extended queries are unsupported; use a non-streaming driver"
            ),
            _ => bail!("unsupported frontend message {}", frame.tag as char),
        }
    }
}

async fn freeze() -> ! {
    pending::<()>().await;
    unreachable!()
}

async fn session(
    mut client: TcpStream,
    upstream: &str,
    id: u64,
    shared: Arc<Shared>,
) -> Result<()> {
    client.set_nodelay(true)?;
    let mut attempts = 0;
    let start = loop {
        let packet = wire::startup(&mut client).await?;
        let code = u32::from_be_bytes(packet[4..8].try_into().unwrap());
        match code {
            80877103 | 80877104 => {
                attempts += 1;
                ensure!(attempts <= 2, "repeated TLS/GSS negotiation");
                client.write_all(b"N").await?;
            }
            196608 => break packet,
            80877102 => bail!("CancelRequest unsupported in bounded crash tests"),
            _ => bail!("unsupported PostgreSQL startup protocol {code}"),
        }
    };
    let mut server = TcpStream::connect(upstream)
        .await
        .context("connect PostgreSQL upstream")?;
    server.set_nodelay(true)?;
    server.write_all(&start).await?;
    // Independent readers are cancellation-safe: select never drops a partially
    // read protocol frame. Authentication can require traffic in both directions.
    let (mut cr, mut cw) = client.into_split();
    let (mut sr, mut sw) = server.into_split();
    let (ftx, mut front) = mpsc::channel(8);
    let (btx, mut back) = mpsc::channel(8);
    let ftask = tokio::spawn(async move {
        loop {
            let f = wire::read(&mut cr).await;
            let stop = !matches!(f, Ok(Some(_)));
            if ftx.send(f).await.is_err() || stop {
                break;
            }
        }
    });
    let btask = tokio::spawn(async move {
        loop {
            let f = wire::read(&mut sr).await;
            let stop = !matches!(f, Ok(Some(_)));
            if btx.send(f).await.is_err() || stop {
                break;
            }
        }
    });
    let result = relay(&mut front, &mut back, &mut cw, &mut sw, id, shared).await;
    ftask.abort();
    btask.abort();
    result
}

async fn relay(
    front: &mut mpsc::Receiver<Result<Option<Frame>>>,
    back: &mut mpsc::Receiver<Result<Option<Frame>>>,
    cw: &mut tokio::net::tcp::OwnedWriteHalf,
    sw: &mut tokio::net::tcp::OwnedWriteHalf,
    id: u64,
    shared: Arc<Shared>,
) -> Result<()> {
    loop {
        tokio::select! {
            f=front.recv()=>{let f=f.context("client reader closed")??.context("client disconnected during authentication")?; ensure!(f.tag==b'p',"unexpected frontend authentication message"); f.write(sw).await?;}
            b=back.recv()=>{
                let b=b.context("backend reader closed")??.context("backend disconnected during authentication")?;
                let ready=b.tag==b'Z';
                if b.tag==b'E' { bail!("PostgreSQL authentication failed (details omitted)"); }
                b.write(cw).await?;
                if ready {break;}
            }
        }
    }
    let mut names = Names::default();
    let mut previous_state = 'I';
    loop {
        let Some(first) = front.recv().await else {
            return Ok(());
        };
        let Some(first) = first? else { return Ok(()) };
        if first.tag == b'X' {
            first.write(sw).await?;
            return Ok(());
        }
        let simple = first.tag == b'Q';
        let mut batch = vec![first];
        let mut size = batch[0].bytes();
        if !simple {
            while batch.last().unwrap().tag != b'S' {
                ensure!(
                    batch.last().unwrap().tag != b'H',
                    "Flush/streaming extended queries unsupported"
                );
                let f = front
                    .recv()
                    .await
                    .context("client reader ended mid-cycle")??
                    .context("client disconnected mid-cycle")?;
                ensure!(f.tag != b'Q' && f.tag != b'X', "invalid mixed query cycle");
                size += f.bytes();
                ensure!(size <= MAX_CYCLE, "frontend cycle buffer limit exceeded");
                batch.push(f);
            }
        }
        // Predict SQL within this cycle, but persist name changes only when
        // PostgreSQL acknowledges them. A rejected Parse must not replace a
        // previously valid named statement in our metadata.
        let mut staged_names = names.clone();
        let mut pending_names: VecDeque<&Frame> = batch
            .iter()
            .filter(|f| matches!(f.tag, b'P' | b'B' | b'C'))
            .collect();
        let mut queries = Vec::new();
        for f in &batch {
            if let Some(sql) = staged_names.observe(f)?
                && queries.last() != Some(&sql)
            {
                queries.push(sql);
            }
        }
        let hash = format!("{:x}", Sha256::digest(queries.join("\0").as_bytes()));
        let armed = shared.armed.load(Ordering::SeqCst);
        let guard = if armed {
            Some(shared.cycle.lock().await)
        } else {
            None
        };
        if shared.tripped.load(Ordering::SeqCst) {
            freeze().await;
        }
        let index = if armed {
            shared.counter.fetch_add(1, Ordering::SeqCst) + 1
        } else {
            0
        };
        let mut meta = json!({"event":"boundary","index":index,"connection":id,"protocol":if simple {"simple"} else {"extended"},"query_sha256":hash,"state_before":previous_state});
        if shared.include_sql {
            meta["sql"] = json!(queries);
        }
        if index > 0 {
            emit(json!({"event":"begin","boundary":meta}));
        }
        if index > 0 && index == shared.cut && shared.phase == "before" {
            shared.tripped.store(true, Ordering::SeqCst);
            emit(json!({"event":"cut","phase":"before","boundary":meta}));
            freeze().await;
        }
        for f in &batch {
            f.write(sw).await?;
        }
        // Withhold the COMPLETE response, not only ReadyForQuery. Some
        // drivers resolve the promise on CommandComplete before reading Z.
        // An implicit extended transaction commits at Sync, not Execute.
        let mut response = Vec::new();
        let mut response_size = 0;
        let mut commands = Vec::new();
        let mut errors = Vec::new();
        let state = loop {
            let b = back
                .recv()
                .await
                .context("backend reader ended mid-cycle")??
                .context("backend disconnected mid-cycle")?;
            ensure!(
                !matches!(b.tag, b'G' | b'H' | b'W' | b'A' | b's'),
                "COPY, notifications and suspended portals unsupported"
            );
            response_size += b.bytes();
            ensure!(
                response_size <= MAX_CYCLE,
                "backend cycle buffer limit exceeded"
            );
            if matches!(b.tag, b'1' | b'2' | b'3') {
                let f = pending_names
                    .pop_front()
                    .context("unexpected name acknowledgement")?;
                let expected = match b.tag {
                    b'1' => b'P',
                    b'2' => b'B',
                    _ => b'C',
                };
                ensure!(f.tag == expected, "out-of-order name acknowledgement");
                names.observe(f)?;
            }
            if b.tag == b'C' {
                commands.push(cstring(&b.body, &mut 0)?);
            }
            if b.tag == b'E' {
                errors.push(error_code(&b.body)?);
            }
            let ready = b.tag == b'Z';
            let state = if ready {
                ensure!(
                    b.body.len() == 1 && b"ITE".contains(&b.body[0]),
                    "invalid ReadyForQuery"
                );
                b.body[0] as char
            } else {
                '?'
            };
            response.push(b);
            if ready {
                break state;
            }
        };
        meta["commands"] = json!(commands);
        meta["sqlstates"] = json!(errors);
        meta["state_after"] = json!(state);
        if index > 0 {
            emit(&meta);
        }
        if index > 0 && index == shared.cut && shared.phase == "after" {
            shared.tripped.store(true, Ordering::SeqCst);
            emit(json!({"event":"cut","phase":"after","boundary":meta}));
            freeze().await;
        }
        for b in &response {
            b.write(cw).await?;
        }
        if simple {
            names.statements.remove("");
            names.portals.remove("");
        }
        if state == 'I' {
            names.portals.clear();
        }
        previous_state = state;
        drop(guard);
    }
}

fn error_code(body: &[u8]) -> Result<String> {
    let mut p = 0;
    while p < body.len() && body[p] != 0 {
        let field = body[p];
        p += 1;
        let value = cstring(body, &mut p)?;
        if field == b'C' {
            return Ok(value);
        }
    }
    Ok("unknown".to_string())
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut listen = "127.0.0.1:0".to_string();
    let mut upstream = None;
    let mut cut = 0;
    let mut phase = "after".to_string();
    let mut include_sql = false;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--listen" => listen = args.next().context("--listen needs address")?,
            "--upstream" => upstream = Some(args.next().context("--upstream needs address")?),
            "--cut" => cut = args.next().context("--cut needs index")?.parse()?,
            "--phase" => phase = args.next().context("--phase needs before/after")?,
            "--include-sql" => include_sql = true,
            _ => bail!("unknown argument {arg}"),
        }
    }
    ensure!(
        phase == "before" || phase == "after",
        "phase must be before or after"
    );
    let listen: std::net::SocketAddr = listen.parse()?;
    ensure!(listen.ip().is_loopback(), "proxy must bind loopback");
    let upstream = upstream.context("--upstream required")?;
    let shared = Arc::new(Shared {
        armed: AtomicBool::new(false),
        tripped: AtomicBool::new(false),
        counter: AtomicU64::new(0),
        cycle: Mutex::new(()),
        cut,
        phase,
        include_sql,
    });
    let listener = TcpListener::bind(listen).await?;
    emit(
        json!({"event":"ready","address":listener.local_addr()?.to_string(),"version":env!("CARGO_PKG_VERSION")}),
    );
    let control = shared.clone();
    tokio::spawn(async move {
        let mut lines = BufReader::new(tokio::io::stdin()).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if line == "arm" {
                if control.armed.swap(true, Ordering::SeqCst) {
                    emit(json!({"event":"fatal","message":"already armed"}));
                    std::process::exit(2);
                }
                emit(json!({"event":"armed"}));
            } else {
                emit(json!({"event":"fatal","message":"unknown control command"}));
                std::process::exit(2);
            }
        }
        // Supervisor disappeared. Do not leave an orphan listener behind.
        std::process::exit(0);
    });
    let mut id = 0;
    loop {
        let (client, _) = listener.accept().await?;
        id += 1;
        let shared = shared.clone();
        let upstream = upstream.clone();
        tokio::spawn(async move {
            if let Err(error) = session(client, &upstream, id, shared).await {
                emit(json!({"event":"fatal","connection":id,"message":format!("{error:#}")}));
                std::process::exit(2);
            }
        });
    }
}
