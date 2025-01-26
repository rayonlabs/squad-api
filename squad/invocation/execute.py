import argparse
import os
import json
import asyncio
import backoff
import squad.database.orms  # noqa
from loguru import logger
from pathlib import Path
from sqlalchemy import select
from squad.auth import generate_auth_token
from squad.database import get_session
from squad.config import settings
from squad.invocation.schemas import Invocation


@backoff.on_exception(
    backoff.constant,
    Exception,
    jitter=None,
    interval=3,
    max_tries=7,
)
async def _download(path):
    try:
        logger.info(f"Attempting to download {path}")
        async with settings.s3_client() as s3:
            filename = Path(path).name
            local_path = os.path.join("/tmp/inputs", filename)
            await s3.download_file(settings.storage_bucket, path, local_path)
            logger.info(f"Successfully downloaded {path} to {local_path}")
    except Exception as exc:
        logger.error(f"FAILED TO DOWNLOAD: {exc}")


async def prepare_execution_environment(invocation_id: str):
    """
    Copy all of the invocation input files to disk from the blob store, and
    generate the code that will be used by the agent for this task.
    """
    os.makedirs("/tmp/inputs", exist_ok=True)
    os.makedirs("/tmp/conf", exist_ok=True)
    async with get_session() as session:
        invocation = (
            (
                await session.execute(
                    select(Invocation).where(Invocation.invocation_id == invocation_id)
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if not invocation:
            raise Exception(f"Invocation does not exist: {invocation_id}")
        if invocation.completed_at:
            raise Exception(f"Invocation already completed: {invocation_id}")

    # Download input files.
    if invocation.inputs:
        await asyncio.gather(*[_download(path) for path in invocation.inputs])

    # Create an auth token to use.
    scopes = [invocation.invocation_id]
    if invocation.source in ["x", "schedule"]:
        scopes.append("x")
    token = generate_auth_token(
        invocation.user_id,
        duration_minutes=60,
        agent_id=invocation.agent_id,
        scopes=scopes,
    )

    # Configure the task, based on the input type.
    configmap, code = invocation.agent.as_executable(task=invocation.task, source=invocation.source)
    configmap["authorization"] = f"Bearer {token}"
    with open("/tmp/conf/configmap.json", "w") as outfile:
        outfile.write(json.dumps(configmap, indent=2))
    with open("/tmp/conf/execute.py", "w") as outfile:
        outfile.write(code)
    logger.info(f"Saved configmap and code for {invocation_id=} from {invocation.source=}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare the execution environment, not run it.",
    )
    parser.add_argument(
        "--id",
        type=str,
        required=True,
        help="The invocation ID to initiaize/execute",
    )
    args = parser.parse_args()
    if args.prepare:
        await prepare_execution_environment(args.id)
    else:
        print("TODO: execute")


if __name__ == "__main__":
    asyncio.run(main())
