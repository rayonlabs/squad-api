{{- define "api.labels" -}}
app.kubernetes.io/name: api
redis-access: "true"
db-access: "true"
{{- end }}

{{- define "xStreamer.labels" -}}
app.kubernetes.io/name: x-streamer
redis-access: "true"
db-access: "true"
{{- end }}

{{- define "xSearcher.labels" -}}
app.kubernetes.io/name: x-searcher
redis-access: "true"
db-access: "true"
{{- end }}

{{- define "redis.labels" -}}
app.kubernetes.io/name: redis
{{- end }}

{{- define "memcached.labels" -}}
app.kubernetes.io/name: memcached
{{- end }}

{{- define "execution.labels" -}}
app.kubernetes.io/name: execution
{{- end }}

{{- define "squad.commonEnv" -}}
- name: SQUAD_API_BASE_URL
  value: https://api.sqd.io
- name: PYTHONWARNINGS
  value: ignore
- name: OPENSEARCH_URL
  value: http://opensearch:9200
- name: DB_POOL_SIZE
  value: "256"
- name: DB_OVERFLOW
  value: "32"
- name: DEFAULT_MAX_STEPS
  value: "25"
- name: TWEET_INDEX_VERSION
  value: "{{ .Values.opensearch.indexConfig.tweets.version }}"
- name: TWEET_INDEX_SHARDS
  value: "{{ .Values.opensearch.indexConfig.tweets.shards }}"
- name: TWEET_INDEX_REPLICAS
  value: "{{ .Values.opensearch.indexConfig.tweets.replicas }}"
- name: MEMORY_INDEX_VERSION
  value: "{{ .Values.opensearch.indexConfig.memories.version }}"
- name: MEMORY_INDEX_SHARDS
  value: "{{ .Values.opensearch.indexConfig.memories.shards }}"
- name: MEMORY_INDEX_REPLICAS
  value: "{{ .Values.opensearch.indexConfig.memories.replicas }}"
- name: OAUTHLIB_INSECURE_TRANSPORT
  value: "1"
{{- end }}

{{- define "squad.agentEnv" }}
- name: DEFAULT_IMAGE_MODEL
  value: {{ .Values.agentConfig.defaults.models.image }}
- name: DEFAULT_VLM_MODEL
  value: {{ .Values.agentConfig.defaults.models.vlm }}
- name: DEFAULT_TEXT_GEN_MODEL
  value: {{ .Values.agentConfig.defaults.models.llm }}
- name: DEFAULT_TTS_VOICE
  value: {{ .Values.agentConfig.defaults.params.tts.voice }}
- name: EXECUTION_PROXY
  value: {{ .Values.agentConfig.execution.proxy.url }}
{{- end }}

{{- define "squad.sensitiveEnv" -}}
- name: MEMCACHED
  value: memcached
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: redis-secret
      key: password
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: postgres-secret
      key: password
- name: POSTGRESQL
  valueFrom:
    secretKeyRef:
      name: postgres-secret
      key: url
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: redis-secret
      key: url
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: s3-credentials
      key: access-key-id
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: s3-credentials
      key: secret-access-key
- name: AWS_ENDPOINT_URL
  valueFrom:
    secretKeyRef:
      name: s3-credentials
      key: endpoint-url
- name: AWS_REGION
  valueFrom:
    secretKeyRef:
      name: s3-credentials
      key: aws-region
- name: STORAGE_BUCKET
  valueFrom:
    secretKeyRef:
      name: s3-credentials
      key: bucket
- name: JWT_PRIVATE_PATH
  value: /etc/jwt-cert/squad_priv.pem
- name: JWT_PUBLIC_PATH
  value: /etc/jwt-cert/squad_pub.pem
- name: X_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: x-secret
      key: api-token
- name: X_APP_ID
  valueFrom:
    secretKeyRef:
      name: x-secret
      key: app-id
- name: X_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: x-secret
      key: client-id
- name: X_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: x-secret
      key: client-secret
- name: AES_SECRET
  valueFrom:
    secretKeyRef:
      name: aes-secret
      key: secret
- name: BRAVE_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: brave-secret
      key: token
{{- end }}
