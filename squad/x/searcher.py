import uuid
import asyncio
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
            auth = "Bearer " + generate_auth_token(agent.user_id, duration_minutes=30)
            for search in agent.x_searches or []:
                search_key = "x:searchfail:" + str(
                    uuid.uuid5(uuid.NAMESPACE_OID, f"{agent.agent_id}:{search}")
                )
                bad_search = await settings.redis_client.get(search_key)
                if bad_search:
                    logger.warning(f"Skipping bad search: {search}")
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
                try:
                    indexed = await find_and_index_tweets(search, auth)
                except Exception:
                    await settings.redis_client.incr(search_key)
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
