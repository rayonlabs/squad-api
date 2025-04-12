"""
Event handlers for invocations.
"""

import asyncio
import uuid
import concurrent.futures
from loguru import logger
from sqlalchemy import event
from squad.config import k8s_job_client
from squad.agent_config import settings as agent_settings
from squad.invocation.schemas import Invocation
from squad.invocation.execute import prepare_execution_package
from kubernetes import client


@event.listens_for(Invocation, "after_insert")
def create_invocation_job(mapper, connection, invocation):
    """
    Automatically trigger k8s job for the invocation when it's created.
    """
    logger.info(f"Generating presigned URL for execution package {invocation.invocation_id}=")

    def run_async_in_thread():
        return asyncio.run(prepare_execution_package(invocation))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_async_in_thread)
        presigned_url = future.result()
    logger.info(f"Presigned URL generated, creating job {invocation.invocation_id=}")
    job_client = k8s_job_client()
    job_id = str(uuid.uuid5(uuid.NAMESPACE_OID, invocation.invocation_id))
    logger.info(f"Creating kubernetes job for invocation_id={invocation.invocation_id}, {job_id=}")
    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            generate_name=f"invocation-{job_id}",
            labels={
                "kueue.x-k8s.io/queue-name": invocation.queue_name,
            },
        ),
        spec=client.V1JobSpec(
            suspend=True,
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
                            resources=client.V1ResourceRequirements(
                                requests={
                                    "cpu": "500m" if "free" in invocation.queue_name else "2",
                                    "memory": "2Gi" if "free" in invocation.queue_name else "4Gi",
                                },
                                limits={
                                    "cpu": "500m" if "free" in invocation.queue_name else "2",
                                    "memory": "2Gi" if "free" in invocation.queue_name else "4Gi",
                                },
                            ),
                            env=[
                                client.V1EnvVar(name="TRANSFORMERS_VERBOSITY", value="error"),
                                client.V1EnvVar(name="AGENT_ID", value=invocation.agent_id),
                                client.V1EnvVar(name="SQUAD_API_BASE_URL", value="http://api:8000"),
                                client.V1EnvVar(name="PYTHONBUFFERED", value="0"),
                                client.V1EnvVar(name="PACKAGE_URL", value=presigned_url),
                                client.V1EnvVar(
                                    name="EXECUTION_TIMEOUT",
                                    value=f"{invocation.agent.max_execution_time}",
                                ),
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
                                client.V1EnvVar(
                                    name="INVOCATION_ID",
                                    value=invocation.invocation_id,
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
            active_deadline_seconds=invocation.agent.max_execution_time + 180,
            ttl_seconds_after_finished=180,
        ),
    )

    # Create job
    try:
        _ = job_client.create_namespaced_job(namespace="squad", body=job)
        logger.success(f"Successfully created job: {job_id}")
    except Exception as e:
        logger.error(f"Failed to create job: {e}")
