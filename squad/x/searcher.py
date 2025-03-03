import uuid
import asyncio
import traceback
import squad.database.orms  # noqa
from loguru import logger
from sqlalchemy import select
from squad.database import get_session
from squad.agent.schemas import Agent
from squad.auth import generate_auth_token
from squad.util import rate_limit
from squad.config import settings
from squad.storage.x import (
    find_and_index_tweets,
)

FIFTEEN_MINUTES = 15 * 60
SEARCH_LIMIT = 300
READ_LIMIT = 500


async def update_index():
    """
    Iterate through the agents and update the tweets index with the
    latest tweets from the agent config's search users/terms.
    """
    async with get_session() as db:
        agents = (await db.execute(select(Agent))).unique().scalars().all()

        for agent in agents:
            # NOTE duplicates don't matter here since the X module skips
            # requests with the same params in short time spans.
            for search in agent.x_searches or []:
                search_key = "x:searchfail:" + str(
                    uuid.uuid5(uuid.NAMESPACE_OID, f"{agent.agent_id}:{search}")
                )
                fail_count = await settings.redis_client.get(search_key)
                if fail_count:
                    try:
                        fail_count = int(fail_count)
                    except ValueError:
                        await settings.redis_client.delete(search_key)
                        fail_count = 0
                    if fail_count >= 3:
                        logger.warning(f"Skipping bad search: {search} {fail_count=}")
                        await settings.redis_client.expire(search_key, 600)
                        continue

                # Check if we should rate limit first (without incrementing)
                while await rate_limit("x_search", SEARCH_LIMIT, FIFTEEN_MINUTES, incr_by=0):
                    logger.warning("X search rate limit, backing off...")
                    await asyncio.sleep(5)
                logger.info(f"Searching X for {search=}")

                # Increment rate limit counters.
                await rate_limit("x_search", SEARCH_LIMIT, FIFTEEN_MINUTES)
                search_key = "x:searchfail:" + str(
                    uuid.uuid5(uuid.NAMESPACE_OID, f"{agent.agent_id}:{search}")
                )

                # Perform the actual search and index the tweets.
                auth = "Bearer " + generate_auth_token(
                    settings.default_user_id, duration_minutes=30
                )
                try:
                    indexed = await find_and_index_tweets(search, auth)
                except Exception as exc:
                    logger.warning(
                        f"Failed performing search: {search}\nException was: {exc}\n{traceback.format_exc()}"
                    )
                    await settings.redis_client.incr(search_key)

                # Update rate limit counters again...
                incr_by = indexed
                while await rate_limit(
                    "x_total_read", READ_LIMIT, FIFTEEN_MINUTES, incr_by=incr_by
                ):
                    logger.warning("X search read limit, backing off...")
                    incr_by = 0
                    await asyncio.sleep(5)


async def main():
    """
    Main loop forever.
    """
    while True:
        logger.info("Updating agent X searches...")
        await update_index()
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
