"""
Agent invocation execution entrypoints.
"""

import io
import argparse
import os
import time
import json
import glob
import asyncio
import backoff
import aiohttp
import tarfile
import tempfile
import squad.database.orms  # noqa
from loguru import logger
from pathlib import Path
from squad.aiosession import SessionManager
from squad.auth import generate_auth_token
from squad.config import settings
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
    """
    Ship a chunk of logs for this invocation.
    """
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


async def prepare_execution_package(invocation: Invocation):
    """
    Copy all of the invocation input files to disk from the blob store, and
    generate the code that will be used by the agent for this task.
    """
    invocation_id = invocation.invocation_id
    if invocation.completed_at:
        raise Exception(f"Invocation already completed: {invocation_id}")
    tar_path = None
    try:
        with tempfile.TemporaryDirectory() as tempdir:
            os.makedirs(os.path.join(tempdir, "inputs"))
            os.makedirs(os.path.join(tempdir, "conf"))

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
            local_paths = []
            if invocation.inputs:
                for path in invocation.inputs:
                    filename = Path(path).name
                    local_paths.append(os.path.join("/tmp/inputs", filename))

            # Configure the task, based on the input type.
            configmap, code = invocation.agent.as_executable(
                task=invocation.task,
                user_id=invocation.user_id,
                source=invocation.source,
                input_files=local_paths,
            )
            configmap["authorization"] = f"Bearer {token}"
            configmap["inputs"] = invocation.inputs
            with open(os.path.join(tempdir, "conf", "configmap.json"), "w") as outfile:
                outfile.write(json.dumps(configmap, indent=2))
            with open(os.path.join(tempdir, "conf", "execute.py"), "w") as outfile:
                outfile.write(code)

            logger.info(f"Saved configmap and code for {invocation_id=} from {invocation.source=}")

            # Create a tarball of the tempdir.
            tar_filename = f"{invocation_id}_package.tar.gz"
            tar_path = os.path.join(tempdir, tar_filename)
            with tarfile.open(tar_path, "w:gz") as tar:
                for dir_name in ["inputs", "conf"]:
                    dir_path = os.path.join(tempdir, dir_name)
                    tar.add(dir_path, arcname=dir_name)

            # Upload the tarball and generate a presigned URL.
            upload_path = f"executions/{invocation_id}/{tar_filename}"
            async with settings.s3_client() as s3:
                with open(tar_path, "rb") as f:
                    await s3.upload_fileobj(
                        io.BytesIO(f.read()),
                        settings.storage_bucket,
                        upload_path,
                    )
            async with settings.s3_client() as s3:
                presigned = await s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": settings.storage_bucket,
                        "Key": upload_path,
                    },
                    ExpiresIn=86400,
                )
                return presigned
    except Exception as exc:
        logger.error(f"Error packaging execution for {invocation.invocation_id}: {exc}")
        raise
    finally:
        if tar_path and os.path.exists(tar_path):
            try:
                os.remove(tar_path)
            except Exception as exc:
                logger.warning(f"Failed to remove temporary tarball {tar_path}: {exc}")


async def _capture_logs(stream: asyncio.StreamReader, queue: asyncio.Queue):
    """
    Log producer, which just adds the log messages to a queue for later.
    """
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded_line = line.decode().rstrip("\n")
        print(decoded_line)
        await queue.put(decoded_line)


async def _log_writer(invocation_id: str, queue: asyncio.Queue):
    """
    Log writer, consolidates into one alog file and ships off to API.
    """
    log_path = f"/tmp/outputs/invocation-{invocation_id}.log"
    buffer = []
    char_count = 0
    with open(log_path, "a+") as outfile:
        while True:
            try:
                line = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if buffer:
                    text = "\n".join(buffer)
                    await _ship_log(invocation_id, text)
                    buffer.clear()
                    char_count = 0
                continue
            if line is None:
                if buffer:
                    outfile.write("\n".join(buffer) + "\n")
                    outfile.flush()
                    text = "\n".join(buffer)
                    await _ship_log(invocation_id, text)
                break
            else:
                outfile.write(line.rstrip("\n") + "\n")
                outfile.flush()
                char_count += len(line)
                if char_count >= 512:
                    if buffer:
                        text = "\n".join(buffer)
                        await _ship_log(invocation_id, text)
                        buffer.clear()
                        char_count = 0
                buffer.append(line.rstrip("\n"))


async def execute(invocation_id):
    """
    Do the thing!
    """
    started_at = time.time()

    # Make sure our execution package URL is set (presigned URL to tarball with code).
    package_url = os.getenv("PACKAGE_URL")
    if not package_url:
        logger.warning("No PACKAGE_URL, nothing to do...")
        return

    # Download the execution package.
    tar_path = f"/tmp/{invocation_id}_package.tar.gz"
    try:
        logger.info(f"Downloading package from URL for invocation {invocation_id}")
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            async with session.get(package_url) as response:
                with open(tar_path, "wb") as f:
                    f.write(await response.read())
        logger.info(f"Extracting package to /tmp for invocation {invocation_id}")
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(path="/tmp")
        logger.info(f"Successfully extracted package for invocation {invocation_id}")
        os.remove(tar_path)
        if not os.path.exists("/tmp/inputs"):
            logger.warning("/tmp/inputs directory not found after extraction")
        if not os.path.exists("/tmp/conf"):
            logger.warning("/tmp/conf directory not found after extraction")
    except Exception as exc:
        logger.error(f"Error downloading/extracting execution package {invocation_id=}: {exc}")
        if os.path.exists(tar_path):
            os.remove(tar_path)
        raise
    os.makedirs("/tmp/outputs", exist_ok=True)

    # Download input files.
    with open("/tmp/conf/configmap.json") as infile:
        config = json.load(infile)
    SQUAD_SM._headers = {"Authorization": config["authorization"]}

    # Download input files.
    if config.get("inputs"):
        await asyncio.gather(*[_download(invocation_id, path) for path in config["inputs"]])

    # Launch subprocess
    log_queue = asyncio.Queue()
    try:
        process = await asyncio.create_subprocess_exec(
            "poetry",
            "run",
            "python",
            "/tmp/conf/execute.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        logger.error(f"Failed to start subprocess: {e}")
        await _mark_complete(invocation_id, error=str(e))
        return

    # Producer tasks for stdout and stderr
    producer_stdout = asyncio.create_task(_capture_logs(process.stdout, log_queue))
    producer_stderr = asyncio.create_task(_capture_logs(process.stderr, log_queue))

    # Single consumer for combined logs
    consumer_task = asyncio.create_task(_log_writer(invocation_id, log_queue))

    failure_reason = None
    try:
        returncode = await process.wait()
        await producer_stdout
        await producer_stderr
        await log_queue.put(None)
        await consumer_task
        delta = time.time() - started_at
        if returncode == 0:
            message = f"Successfully executed agent task in {round(delta, 5)} seconds, pushing..."
            logger.success(message)
            await _ship_log(invocation_id, message)
        else:
            message = f"Agent execution failed after {round(delta, 5)} seconds!"
            logger.error(message)
            failure_reason = f"Bad exit code from subprocess: {returncode}"
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
        "--id",
        type=str,
        required=True,
        help="The invocation ID to initialize/execute",
    )
    args = parser.parse_args()
    await execute(args.id[3:])


if __name__ == "__main__":
    asyncio.run(main())
