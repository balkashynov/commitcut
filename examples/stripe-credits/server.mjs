import http from 'node:http';
import pg from 'pg';
import Stripe from 'stripe';

const pool = new pg.Pool({connectionString: process.env.DATABASE_URL, max: 1});
const stripe = new Stripe('sk_test_commitcut_local_fixture_only');
const mode = process.env.HANDLER_MODE || 'claim-first';

async function handle(event) {
  const db = await pool.connect();
  const invoice = event.data.object;
  const key = event.id;
  const query = (name, text, values) => db.query({name, text, values});
  try {
    if (mode === 'atomic') await db.query('BEGIN');
    const seen = await query('seen', 'SELECT event_id FROM processed_events WHERE event_id = $1', [key]);
    if (seen.rowCount) {
      if (mode === 'atomic') await db.query('COMMIT');
      return;
    }
    const claim = () => query('claim', 'INSERT INTO processed_events (event_id) VALUES ($1)', [key]);
    const grant = () => query('grant', 'UPDATE accounts SET credits = credits + $1 WHERE customer_id = $2', [invoice.metadata.credits, invoice.customer]);
    if (mode === 'effect-first') {
      await grant();
      await claim();
    } else {
      await claim();
      await grant();
    }
    if (mode === 'atomic') await db.query('COMMIT');
  } catch (error) {
    if (mode === 'atomic') await db.query('ROLLBACK');
    throw error;
  } finally {
    db.release();
  }
}

const server = http.createServer(async (request, response) => {
  if (request.url === '/health') {response.writeHead(200).end('ok'); return;}
  if (request.method !== 'POST' || request.url !== '/webhook') {response.writeHead(404).end(); return;}
  try {
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 1000000) throw new Error('fixture too large');
      chunks.push(chunk);
    }
    const event = stripe.webhooks.constructEvent(Buffer.concat(chunks), request.headers['stripe-signature'], process.env.STRIPE_WEBHOOK_SECRET);
    if (event.type !== 'invoice.paid') throw new Error('expected invoice.paid fixture');
    await handle(event);
    response.writeHead(204).end();
  } catch (error) {
    console.error(error);
    response.writeHead(400).end('failed');
  }
});
server.listen(Number(process.env.PORT), '127.0.0.1');
