"""
Event handlers for invocations.
"""

import uuid
from loguru import logger
from sqlalchemy import event
from squad.config import k8s_job_client
from squad.agent_config import settings as agent_settings
from squad.invocation.schemas import Invocation
import os
from kubernetes import client


def create_env_var_from_secret(name: str, secret_name: str, secret_key: str):
    return client.V1EnvVar(
        name=name,
        value_from=client.V1EnvVarSource(
            secret_key_ref=client.V1SecretKeySelector(name=secret_name, key=secret_key)
        ),
    )


def create_env_var_from_os(name: str, default=None):
    value = os.environ.get(name, default)
    if value is not None:
        return client.V1EnvVar(name=name, value=str(value))
    return None


def get_environment_variables():
    env_vars = []
    direct_vars = [
        "SQUAD_API_BASE_URL",
        "PYTHONWARNINGS",
        "OPENSEARCH_URL",
        "DB_POOL_SIZE",
        "DB_OVERFLOW",
        "DEFAULT_MAX_STEPS",
        "TWEET_INDEX_VERSION",
        "TWEET_INDEX_SHARDS",
        "TWEET_INDEX_REPLICAS",
        "MEMORY_INDEX_VERSION",
        "MEMORY_INDEX_SHARDS",
        "MEMORY_INDEX_REPLICAS",
        "OAUTHLIB_INSECURE_TRANSPORT",
        "DEFAULT_IMAGE_MODEL",
        "DEFAULT_VLM_MODEL",
        "DEFAULT_TEXT_GEN_MODEL",
        "DEFAULT_TTS_VOICE",
        "JWT_PRIVATE_PATH",
        "JWT_PUBLIC_PATH",
    ]
    for var_name in direct_vars:
        env_var = create_env_var_from_os(var_name)
        if env_var:
            env_vars.append(env_var)
    secret_vars = [
        ("REDIS_PASSWORD", "redis-secret", "password"),
        ("POSTGRES_PASSWORD", "postgres-secret", "password"),
        ("POSTGRESQL", "postgres-secret", "url"),
        ("REDIS_URL", "redis-secret", "url"),
        ("AWS_ACCESS_KEY_ID", "s3-credentials", "access-key-id"),
        ("AWS_SECRET_ACCESS_KEY", "s3-credentials", "secret-access-key"),
        ("AWS_ENDPOINT_URL", "s3-credentials", "endpoint-url"),
        ("AWS_REGION", "s3-credentials", "aws-region"),
        ("STORAGE_BUCKET", "s3-credentials", "bucket"),
        ("X_API_TOKEN", "x-secret", "api-token"),
        ("X_APP_ID", "x-secret", "app-id"),
        ("X_CLIENT_ID", "x-secret", "client-id"),
        ("X_CLIENT_SECRET", "x-secret", "client-secret"),
        ("AES_SECRET", "aes-secret", "secret"),
        ("BRAVE_API_TOKEN", "brave-secret", "token"),
    ]
    for env_name, secret_name, secret_key in secret_vars:
        if os.environ.get(env_name):
            env_vars.append(create_env_var_from_secret(env_name, secret_name, secret_key))
    env_vars.append(client.V1EnvVar(name="SQUAD_API_BASE_URL", value="http://api:8000"))
    return env_vars


@event.listens_for(Invocation, "after_insert")
def create_invocation_job(mapper, connection, invocation):
    """
    Automatically trigger k8s job for the invocation when it's created.
    """
    job_client = k8s_job_client()
    job_id = str(uuid.uuid5(uuid.NAMESPACE_OID, invocation.invocation_id))
    logger.info(f"Creating kubernetes job for invocation_id={invocation.invocation_id}, {job_id=}")
    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=f"invocation-{job_id}",
        ),
        spec=client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={
                        "app": "execution",
                        "agent-id": invocation.agent_id,
                        "user-id": invocation.user_id,
                    }
                ),
                spec=client.V1PodSpec(
                    volumes=[
                        client.V1Volume(
                            name="tmpdir", empty_dir=client.V1EmptyDirVolumeSource(size_limit="4Gi")
                        ),
                        client.V1Volume(
                            name="jwt-cert",
                            secret=client.V1SecretVolumeSource(
                                secret_name="jwt-cert",
                                items=[
                                    client.V1KeyToPath(key="squad_pub.pem", path="squad_pub.pem"),
                                    client.V1KeyToPath(key="squad_priv.pem", path="squad_priv.pem"),
                                ],
                            ),
                        ),
                    ],
                    init_containers=[
                        client.V1Container(
                            name="prepare",
                            image="parachutes/squad-worker:latest",
                            image_pull_policy="Always",
                            command=[
                                "poetry",
                                "run",
                                "python",
                                "squad/invocation/execute.py",
                                "--prepare",
                                "--id",
                                f"id:{invocation.invocation_id}",
                            ],
                            env=get_environment_variables(),
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="jwt-cert", mount_path="/etc/jwt-cert", read_only=True
                                ),
                                client.V1VolumeMount(name="tmpdir", mount_path="/tmp"),
                            ],
                        )
                    ],
                    containers=[
                        client.V1Container(
                            name="execute",
                            image="parachutes/squad-worker:latest",
                            image_pull_policy="Always",
                            command=[
                                "poetry",
                                "run",
                                "python",
                                "squad/invocation/execute.py",
                                "--id",
                                f"id:{invocation.invocation_id}",
                            ],
                            env=[
                                client.V1EnvVar(name="PYTHONWARNINGS", value="ignore"),
                                client.V1EnvVar(name="AGENT_ID", value=invocation.agent_id),
                                client.V1EnvVar(name="SQUAD_API_BASE_URL", value="http://api:8000"),
                                client.V1EnvVar(
                                    name="HTTP_PROXY", value=agent_settings.execution_proxy
                                ),
                                client.V1EnvVar(
                                    name="HTTPS_PROXY", value=agent_settings.execution_proxy
                                ),
                                client.V1EnvVar(
                                    name="NO_PROXY",
                                    value="api,api:8000,api.chutes.ai,*.chutes.ai,.chutes.ai,localhost,127.0.0.1,127.0.0.1:8000,api.squad,api.squad.svc.cluster.local,api.squad:8000,api.squad.svc.cluster.local:8000",
                                ),
                            ],
                            volume_mounts=[
                                client.V1VolumeMount(name="tmpdir", mount_path="/tmp"),
                            ],
                        )
                    ],
                    restart_policy="Never",
                ),
            ),
            backoff_limit=0,
            ttl_seconds_after_finished=180,
        ),
    )

    # Create job
    try:
        job_client.create_namespaced_job(namespace="squad", body=job)
        logger.success(f"Successfully created job: {job_id}")
    except Exception as e:
        logger.error(f"Failed to create job: {e}")
