-- migrate:up
DO $$
BEGIN
  IF NOT EXISTS (select from byok_secrets WHERE user_id = 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f' and name = 'AWS Credentials') THEN
    INSERT INTO byok_secrets (secret_id, name, description, header_key, user_id, public, url_patterns) VALUES (gen_random_uuid(), 'AWS Credentials', 'AWS Credentials', '_', 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f', true, '{*}');
  END IF;
  IF NOT EXISTS (select from byok_secrets WHERE user_id = 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f' and name = 'GCS Credentials') THEN
    INSERT INTO byok_secrets (secret_id, name, description, header_key, user_id, public, url_patterns) VALUES (gen_random_uuid(), 'GCS Credentials', 'GCS service account JSON Credentials', '_', 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f', true, '{*}');
  END IF;
  IF NOT EXISTS (select from byok_secrets WHERE user_id = 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f' and name = 'OpenAI Credentials') THEN
    INSERT INTO byok_secrets (secret_id, name, description, header_key, user_id, public, url_patterns) VALUES (gen_random_uuid(), 'OpenAI Credentials', 'OpenAI/ChatGPT credentials', '_', 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f', true, '{https://api.openai.com/*}');
  END IF;
  IF NOT EXISTS (select from byok_secrets WHERE user_id = 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f' and name = 'Anthropic Credentials') THEN
    INSERT INTO byok_secrets (secret_id, name, description, header_key, user_id, public, url_patterns) VALUES (gen_random_uuid(), 'Anthropic Credentials', 'Anthropic/Claude credentials', '_', 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f', true, '{https://api.anthropic.com/*}');
  END IF;
  IF NOT EXISTS (select from byok_secrets WHERE user_id = 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f' and name = 'OpenRouter Credentials') THEN
    INSERT INTO byok_secrets (secret_id, name, description, header_key, user_id, public, url_patterns) VALUES (gen_random_uuid(), 'OpenRouter Credentials', 'OpenRouter credentials', '_', 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f', true, '{https://openrouter.ai/*}');
  END IF;
END
$$;

-- migrate:down
delete from byok_secrets WHERE user_id = 'dff3e6bb-3a6b-5a2b-9c48-da3abcd5ca5f') and name in ('AWS Credentials', 'GCS Credentials', 'OpenAI Credentials', 'Anthropic Credentials', 'OpenRouter Credentials');
