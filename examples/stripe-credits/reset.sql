DROP TABLE IF EXISTS processed_events;
DROP TABLE IF EXISTS accounts;
CREATE TABLE processed_events (event_id text PRIMARY KEY);
CREATE TABLE accounts (customer_id text PRIMARY KEY, credits integer NOT NULL);
INSERT INTO accounts VALUES ('cus_commitcut', 0);
