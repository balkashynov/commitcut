//! PostgreSQL v3 framing. Length includes its own four bytes, never the tag.
use anyhow::{Result, bail, ensure};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub const MAX_FRAME: usize = 16 * 1024 * 1024;
pub const MAX_CYCLE: usize = 32 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct Frame {
    pub tag: u8,
    pub body: Vec<u8>,
}

impl Frame {
    pub fn bytes(&self) -> usize {
        self.body.len() + 5
    }
    pub async fn write<W: AsyncWrite + Unpin>(&self, w: &mut W) -> Result<()> {
        w.write_u8(self.tag).await?;
        w.write_u32((self.body.len() + 4) as u32).await?;
        w.write_all(&self.body).await?;
        Ok(())
    }
}

pub async fn read<R: AsyncRead + Unpin>(r: &mut R) -> Result<Option<Frame>> {
    // Distinguish clean EOF before a frame from a truncated frame.
    let mut tag = [0];
    if r.read(&mut tag).await? == 0 {
        return Ok(None);
    }
    let len = r.read_u32().await? as usize;
    ensure!(
        (4..=MAX_FRAME).contains(&len),
        "invalid or oversized PostgreSQL frame length {len}"
    );
    let mut body = vec![0; len - 4];
    r.read_exact(&mut body).await?;
    Ok(Some(Frame { tag: tag[0], body }))
}

pub async fn startup<R: AsyncRead + Unpin>(r: &mut R) -> Result<Vec<u8>> {
    let len = r.read_u32().await? as usize;
    ensure!(
        (8..=10_000).contains(&len),
        "invalid startup packet length {len}"
    );
    let mut out = Vec::with_capacity(len);
    out.extend_from_slice(&(len as u32).to_be_bytes());
    out.resize(len, 0);
    r.read_exact(&mut out[4..]).await?;
    Ok(out)
}

pub fn cstring(body: &[u8], offset: &mut usize) -> Result<String> {
    ensure!(*offset < body.len(), "missing protocol string");
    let rest = &body[*offset..];
    let Some(end) = rest.iter().position(|&b| b == 0) else {
        bail!("unterminated protocol string")
    };
    let s = std::str::from_utf8(&rest[..end])?.to_owned();
    *offset += end + 1;
    Ok(s)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn frame_roundtrip_and_eof() {
        let f = Frame {
            tag: b'Q',
            body: b"select 1\0".to_vec(),
        };
        let mut bytes = Vec::new();
        f.write(&mut bytes).await.unwrap();
        let mut src = &bytes[..];
        assert_eq!(read(&mut src).await.unwrap().unwrap().body, f.body);
        assert!(read(&mut src).await.unwrap().is_none());
    }
    #[tokio::test]
    async fn corrupt_lengths_and_truncation_rejected() {
        for bytes in [
            vec![b'Q', 0, 0, 0, 3],
            vec![b'Q', 255, 255, 255, 255],
            vec![b'Q', 0, 0, 0, 9, 1],
        ] {
            assert!(read(&mut &bytes[..]).await.is_err());
        }
    }
    #[test]
    fn strings_are_bounded() {
        assert!(cstring(b"unterminated", &mut 0).is_err());
        assert!(cstring(b"", &mut 0).is_err());
        let mut offset = 0;
        assert_eq!(cstring(b"name\0sql\0", &mut offset).unwrap(), "name");
        assert_eq!(cstring(b"name\0sql\0", &mut offset).unwrap(), "sql");
    }
}
