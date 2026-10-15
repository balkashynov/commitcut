// The vendored helper is byte-for-byte upstream. This is a small dependency
// harness, not the complete Wasp application or its deployment environment.
import http from 'node:http';
import {PrismaClient} from '@prisma/client';
import Stripe from 'stripe';
import {processWebhookEventOnce} from './vendor/processedWebhookEvent.ts';
const prisma = new PrismaClient();
const stripe = new Stripe('sk_test_commitcut_local_fixture_only');
const server = http.createServer(async (request, response) => {
  if (request.url === '/health') {response.writeHead(200).end('ok'); return;}
  try {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const event = stripe.webhooks.constructEvent(Buffer.concat(chunks), request.headers['stripe-signature']!, process.env.STRIPE_WEBHOOK_SECRET!);
    const identity = {paymentProcessorEventId: event.id, eventType: event.type};
    const invoice = event.data.object as Stripe.Invoice;
    const credit = async (db: any) => {
      await db.account.update({where: {customerId: invoice.customer as string}, data: {credits: {increment: 10}}});
    };
    if (process.env.HANDLER_MODE === 'transactional-control') {
      await prisma.$transaction(async (tx) => {
        const claimed = await tx.$executeRaw`INSERT INTO "ProcessedWebhookEvent" ("id", "paymentProcessorEventId", "eventType") VALUES (${event.id}, ${event.id}, ${event.type}) ON CONFLICT ("paymentProcessorEventId") DO NOTHING`;
        if (claimed === 1) await credit(tx);
      });
    } else {
      await processWebhookEventOnce(identity, prisma.processedWebhookEvent, () => credit(prisma));
    }
    response.writeHead(204).end();
  } catch (error) {
    console.error(error);
    response.writeHead(400).end('failed');
  }
});
server.listen(Number(process.env.PORT), '127.0.0.1');
