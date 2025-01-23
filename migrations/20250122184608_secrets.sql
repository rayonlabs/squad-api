-- migrate:up
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS secrets (id TEXT NOT NULL PRIMARY KEY, key TEXT NOT NULL);
INSERT INTO secrets (id, key) 
  SELECT 'x', encode(gen_random_bytes(16), 'hex')
  WHERE NOT EXISTS (SELECT 1 FROM secrets WHERE id = 'x');

-- migrate:down
DROP TABLE IF EXISTS secrets;
