DROP TABLE IF EXISTS "ProcessedWebhookEvent";
DROP TABLE IF EXISTS "Account";
CREATE TABLE "ProcessedWebhookEvent" (
  "id" text PRIMARY KEY,
  "paymentProcessorEventId" text UNIQUE NOT NULL,
  "eventType" text NOT NULL,
  "createdAt" timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE "Account" ("customerId" text PRIMARY KEY, "credits" integer NOT NULL DEFAULT 0);
INSERT INTO "Account" VALUES ('cus_commitcut', 0);
