"""
Agent invocation execution entrypoints.
"""

import argparse
import os
import time
import json
import glob
import asyncio
import backoff
import aiohttp
import squad.database.orms  # noqa
from loguru import logger
from pathlib import Path
from sqlalchemy import select
from squad.aiosession import SessionManager
from squad.auth import generate_auth_token
from squad.database import get_session
from squad.config import settings
from squad.agent_config import settings as agent_settings
from squad.invocation.schemas import Invocation

SQUAD_SM = SessionManager(base_url=settings.squad_api_base_url)


@backoff.on_exception(
    backoff.constant,
    Exception,
    jitter=None,
    interval=3,
    max_tries=7,
)
async def _download(invocation_id, path):
    try:
        logger.info(f"Attempting to download {path}")
        async with SQUAD_SM.get_session() as session:
            filename = Path(path).name
            local_path = os.path.join("/tmp/inputs", filename)
            async with session.get(f"/invocations/{invocation_id}/inputs/{path}") as resp:
                with open(local_path, "wb") as outfile:
                    outfile.write(await resp.read())
            logger.info(f"Successfully downloaded {path} to {local_path}")
            return local_path
    except Exception as exc:
        logger.error(f"FAILED TO DOWNLOAD: {exc}")


@backoff.on_exception(
    backoff.constant,
    Exception,
    jitter=None,
    interval=3,
    max_tries=7,
)
async def _ship_log(invocation_id: str, log: str):
    async with SQUAD_SM.get_session() as session:
        await session.post(f"/invocations/{invocation_id}/log", json={"log": log})


@backoff.on_exception(
    backoff.constant,
    Exception,
    jitter=None,
    interval=3,
    max_tries=7,
)
async def _mark_complete(invocation_id: str, error: str = None):
    async with SQUAD_SM.get_session() as session:
        if error:
            async with session.post(
                f"/invocations/{invocation_id}/fail", json={"error": error}
            ) as _:
                logger.info("Successfully marked the invocation as failed.")
        else:
            with open("/tmp/outputs/_final_answer.json") as infile:
                final_answer = json.load(infile)
                async with session.post(
                    f"/invocations/{invocation_id}/complete", json={"answer": final_answer}
                ) as _:
                    logger.success("Successfully marked the invocation as completed!")


@backoff.on_exception(
    backoff.constant,
    Exception,
    jitter=None,
    interval=3,
    max_tries=7,
)
async def _upload_file(invocation_id: str, path: str):
    message = f"Attempting to upload output file {path}"
    logger.info(message)
    form = aiohttp.FormData()
    form.add_field("files", open(path, "rb"), filename=os.path.basename(path))
    await _ship_log(invocation_id, message)
    async with SQUAD_SM.get_session() as session:
        async with session.post(f"/invocations/{invocation_id}/upload", data=form) as _:
            logger.success(f"Uploaded one file: {path}")


async def prepare_execution_environment(invocation_id: str):
    """
    Copy all of the invocation input files to disk from the blob store, and
    generate the code that will be used by the agent for this task.
    """
    os.makedirs("/tmp/inputs", exist_ok=True)
    os.makedirs("/tmp/conf", exist_ok=True)
    os.makedirs("/tmp/outputs", exist_ok=True)
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

    # Create an auth token to use.
    scopes = [invocation_id]
    if invocation.source in ["x", "schedule"]:
        scopes.append("x")
    token = generate_auth_token(
        invocation.user_id,
        duration_minutes=60,
        agent_id=invocation.agent_id,
        scopes=scopes,
    )
    SQUAD_SM._headers = {"Authorization": f"Bearer {token}"}

    # Download input files.
    local_paths = None
    if invocation.inputs:
        local_paths = await asyncio.gather(
            *[_download(invocation_id, path) for path in invocation.inputs]
        )

    # Configure the task, based on the input type.
    configmap, code = invocation.agent.as_executable(
        task=invocation.task, source=invocation.source, input_files=local_paths
    )
    configmap["authorization"] = f"Bearer {token}"
    with open("/tmp/conf/configmap.json", "w") as outfile:
        outfile.write(json.dumps(configmap, indent=2))
    with open("/tmp/conf/execute.py", "w") as outfile:
        outfile.write(code)
    logger.info(f"Saved configmap and code for {invocation_id=} from {invocation.source=}")


async def execute(invocation_id):
    """
    Do the thing!
    """
    started_at = time.time()
    with open("/tmp/conf/configmap.json") as infile:
        config = json.load(infile)
    SQUAD_SM._headers = {"Authorization": config["authorization"]}

    # Log collector.
    async def _capture_logs(stream, name):
        nonlocal invocation_id
        log_method = logger.info if name == "stdout" else logger.warning
        with open(f"/tmp/outputs/_{name}.log", "a+") as outfile:
            while True:
                line = await stream.readline()
                if line:
                    decoded_line = line.decode().strip()
                    log_method(decoded_line)
                    outfile.write(decoded_line + "\n")
                    await _ship_log(invocation_id, decoded_line)
                else:
                    await _ship_log(invocation_id, "DONE")
                    logger.info(f"Done logging: {name}")
                    break

    # Execute.
    failure_reason = None
    try:
        process = await asyncio.create_subprocess_exec(
            "poetry",
            "run",
            "python",
            "/tmp/conf/execute.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(
            asyncio.gather(
                _capture_logs(process.stdout, "stdout"),
                _capture_logs(process.stderr, "stderr"),
                process.wait(),
            ),
            timeout=agent_settings.timeout,
        )
        delta = time.time() - started_at
        if process.returncode == 0:
            message = f"Successfull executed agent task in {round(delta, 5)} seconds, pushing..."
            logger.success(message)
            await _ship_log(invocation_id, message)
        else:
            message = f"Agent execution failed after {round(delta, 5)} seconds!"
            logger.error(message)
            failure_reason = f"Bad exit code from subprocess: {process.returncode}"
            await _ship_log(invocation_id, message)
    except asyncio.TimeoutError:
        delta = time.time() - started_at
        message = f"Agent execution timeout after {round(delta, 5)} seconds!"
        logger.error(message)
        failure_reason = message
        try:
            await _ship_log(invocation_id, message)
            process.kill()
            await process.communicate()
        except Exception:
            ...
    except Exception as exc:
        delta = time.time() - started_at
        message = f"Unhandled exception executing agent: {exc}"
        failure_reason = message
        logger.error(message)
        try:
            await _ship_log(invocation_id, message)
            process.kill()
            await process.communicate()
        except Exception:
            ...

    # Final step, upload all logs, output files, etc.
    files_to_upload = []
    for path in glob.glob("/tmp/outputs/*", recursive=True):
        if os.path.isfile(path):
            files_to_upload.append(path)
    message = f"Attempting to upload output files: {files_to_upload}"
    logger.info(message)
    await _ship_log(invocation_id, message)
    for path in files_to_upload:
        try:
            await _upload_file(invocation_id, path)
        except Exception as exc:
            logger.error(f"Failed file upload: {exc}")

    # Final status.
    await _mark_complete(invocation_id, error=failure_reason)
    await SQUAD_SM.close()


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
        help="The invocation ID to initialize/execute",
    )
    args = parser.parse_args()
    if args.prepare:
        await prepare_execution_environment(args.id[3:])
    else:
        await execute(args.id[3:])


if __name__ == "__main__":
    asyncio.run(main())
