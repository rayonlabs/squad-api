-- migrate:up
INSERT INTO secrets (id, key)
  SELECT 'byok', encode(gen_random_bytes(16), 'hex')
  WHERE NOT EXISTS (SELECT 1 FROM secrets WHERE id = 'byok');

-- migrate:down
DELETE FROM secrets WHERE id = 'byok';
