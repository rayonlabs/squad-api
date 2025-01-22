-- migrate:up
CREATE TABLE IF NOT EXISTS secrets (id TEXT NOT NULL PRIMARY KEY, key TEXT NOT NULL);

-- migrate:down
DROP TABLE IF EXISTS secrets;
