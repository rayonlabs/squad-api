import asyncio
import time
import squad.database.orms  # noqa
from loguru import logger
from tweepy import StreamRule
from typing import List, Set, Dict, Any
from sqlalchemy import select, func
from tweepy.asynchronous import AsyncStreamingClient
from tweepy.errors import TweepyException
from squad.config import settings
from squad.util import rate_limit
from squad.database import get_session
from squad.agent.schemas import Agent, get_by_x


class XR:
    """
    X stream processor.
    """

    MAX_RULES = 1000

    def __init__(self):
        self.running = False
        self._runtime = time.time()
        self.stream: AsyncStreamingClient | None = None

    async def start(self):
        """
        Start the X stream listener.
        """
        if self.running:
            return
        self.running = True
        self.stream = AsyncStreamingClient(
            bearer_token=settings.tweepy_client.bearer_token, wait_on_rate_limit=True
        )
        await self._sync_rules()
        asyncio.create_task(self._monitor_changes())
        await self._start_stream()

    async def _monitor_changes(self):
        """
        Periodically check for agent updates so we can adjust our rules accordingly.
        """
        while self.running:
            await self._sync_rules()
            await asyncio.sleep(60)

    async def stop(self):
        """
        Stop the stream listener.
        """
        self.running = False
        if self.stream:
            await self.stream.disconnect()

    async def _get_active_usernames(self) -> Set[str]:
        """
        Load active agent usernames.
        """
        return {"elonmusk"}

        async with get_session() as db:
            result = await db.execute(
                select(Agent.x_username)
                .where(Agent.x_username.isnot(None))
                .where(Agent.x_access_token.isnot(None))
                .order_by(func.coalesce(Agent.x_last_mentioned_at, func.now()).desc())
                .limit(self.MAX_RULES)
            )
            return {username for (username,) in result.fetchall()}

    async def _sync_rules(self):
        """
        Keep the X API stream rules up-to-date.
        """
        try:
            current_rules = await self.stream.get_rules()
            rule_map = {}
            for rule in current_rules.data or []:
                rule_map[rule.value] = rule.id
            current_rule_values = set(rule_map)
            db_usernames = await self._get_active_usernames()
            rules_to_add = {f"@{u}" for u in db_usernames} - current_rule_values
            rules_to_remove = current_rule_values - {f"@{u}" for u in db_usernames}
            for rule in rules_to_remove:
                await self.stream.delete_rules([rule_map[rule] for rule in rules_to_remove])
            if rules_to_add:
                await self.stream.add_rules([StreamRule(rule) for rule in rules_to_add])
            self._runtime = time.time()
        except TweepyException as e:
            logger.error(f"Error syncing X rules, keeping as-is for now: {e}")

    async def _start_stream(self):
        """
        Listen for stream events.
        """

        async def on_tweet(tweet: Dict[str, Any]):
            mentioned_users = tweet.get("entities", {}).get("mentions", [])
            await self._process_mentions(
                tweet_id=tweet["id"],
                author_id=tweet.get("author_id"),
                mentions=[m["username"] for m in mentioned_users],
                text=tweet["text"],
            )

        async def on_error(error):
            logger.error(f"X stream error: {error}")
            if self.running:
                await asyncio.sleep(5)
                await self._start_stream()

        self.stream.on_tweet = on_tweet
        self.stream.on_error = on_error
        await self.stream.filter(tweet_fields=["author_id", "entities"])

    async def _process_mentions(
        self, tweet_id: str, author_id: str, mentions: List[str], text: str
    ):
        for username in mentions:
            agent = await get_by_x(username, runtime=self._runtime)
            if not agent:
                logger.debug(f"Skipping tweet mentioning: {username}")
            else:
                logger.info(f"Received tweet: {tweet_id=} {author_id=} {mentions=}")
                if await rate_limit(f"agent_x_call:{agent.agent_id}", 10, 60):
                    logger.warning(f"Rate limit exceeded for {agent.agent_id=} {username=}")
                    continue
                logger.error(f"TODO: X trigger for {agent.agent_id=} {username=}")


async def main():
    """
    Main loop.
    """
    x = XR()
    await x.start()
    while True:
        await asyncio.sleep(1)
        if not x.running:
            break


if __name__ == "__main__":
    asyncio.run(main())
