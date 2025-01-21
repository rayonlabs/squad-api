"""
Main API entrypoint.
"""

import os
import glob
import asyncio
from urllib.parse import quote
from contextlib import asynccontextmanager
from loguru import logger
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
import squad.database.orms  # noqa: F401

# from squad.tool.router import router as tool_router
from squad.data.router import router as data_router
from squad.database import Base, engine
from squad.config import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    Execute all initialization/startup code, e.g. ensuring tables exist and such.
    """
    FastAPICache.init(RedisBackend(settings.redis_client), prefix="squad-api-cache")

    # Normal table creation stuff.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Manual DB migrations.
    migrations_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
    if not os.path.exists(migrations_dir) or not glob.glob(os.path.join(migrations_dir, "*.sql")):
        logger.info(f"No migrations to run (yet): {migrations_dir}")
        yield
        return
    db_url = quote(settings.sqlalchemy.replace("+asyncpg", ""), safe=":/@")
    if "127.0.0.1" in db_url or "@postgres:" in db_url:
        db_url += "?sslmode=disable"

    # dbmate migrations, make sure we only run them in a single process since we use workers > 1
    worker_pid_file = "/tmp/api.pid"
    is_migration_process = False
    try:
        if not os.path.exists(worker_pid_file):
            with open(worker_pid_file, "x") as outfile:
                outfile.write(str(os.getpid()))
            is_migration_process = True
        else:
            with open(worker_pid_file, "r") as infile:
                designated_pid = int(infile.read().strip())
            is_migration_process = os.getpid() == designated_pid
    except FileExistsError:
        with open(worker_pid_file, "r") as infile:
            designated_pid = int(infile.read().strip())
        is_migration_process = os.getpid() == designated_pid
    if not is_migration_process:
        yield
        return

    # Run the migrations.
    process = await asyncio.create_subprocess_exec(
        "dbmate",
        "--url",
        db_url,
        "--migrations-dir",
        migrations_dir,
        "migrate",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def log_migrations(stream, name):
        log_method = logger.info if name == "stdout" else logger.warning
        while True:
            line = await stream.readline()
            if line:
                decoded_line = line.decode().strip()
                log_method(decoded_line)
            else:
                break

    await asyncio.gather(
        log_migrations(process.stdout, "stdout"),
        log_migrations(process.stderr, "stderr"),
        process.wait(),
    )
    if process.returncode == 0:
        logger.success("successfull applied all DB migrations")
    else:
        logger.error(f"failed to run db migrations returncode={process.returncode}")

    yield


# FastAPI init + routes.
app = FastAPI(default_response_class=ORJSONResponse, lifespan=lifespan)
# app.include_router(tool_router, prefix="/tools", tags=["Tools"])
app.include_router(data_router, prefix="/data", tags=["Data"])

# Ping endpoint for k8s probes.
app.get("/ping")(lambda: {"message": "pong"})
