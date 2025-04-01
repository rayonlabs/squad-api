import asyncio
import time
import json
import traceback
from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Dict, List
from sqlalchemy import select, update, func
from tweepy import Client as TweepyClient
from tweepy.errors import TweepyException
from squad.auth import generate_auth_token
from squad.config import settings
from squad.util import rate_limit, now_str
from squad.database import get_session
from squad.agent.schemas import Agent, AgentXInteraction
from squad.invocation.schemas import get_unique_id, Invocation
from squad.storage.x import (
    index_tweets,
    tweet_to_index_format,
    get_users_by_id,
    get_and_index_tweets,
)
import squad.database.orms  # noqa


# Processing rate limit.
RATE_LIMIT = 100
RATE_LIMIT_WINDOW = 60

# Tweet fields to retrieve
TWEET_FIELDS = [
    "author_id",
    "entities",
    "public_metrics",
    "attachments",
    "created_at",
    "referenced_tweets",
]

# Maximum tweets to fetch per request.
MAX_RESULTS = 20


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


async def _create_invocation(agent: Agent, tweet: Dict):
    """
    Create the invocation, which in turn will trigger the event.
    """
    task_text = "You have received the following tweet:\n" + json.dumps(
        tweet, cls=DateTimeEncoder, indent=2
    )
    try:
        invocation_id = await get_unique_id()
        async with get_session() as session:
            invocation = Invocation(
                invocation_id=invocation_id,
                agent_id=agent.agent_id,
                user_id=agent.user_id,
                task=task_text,
                source="x",
                public=agent.public,
            )
            invocation.agent = agent
            session.add(invocation)
            await session.commit()
            await session.refresh(invocation)
            await settings.redis_client.xadd(
                invocation.stream_key,
                {
                    "data": json.dumps(
                        {"log": "Queued agent call from X.", "timestamp": now_str()}
                    ).encode()
                },
            )
            logger.success(
                f"Successfully triggered invocation of {agent.agent_id=} {invocation_id=}"
            )
    except Exception as exc:
        logger.error(f"Failed to create invocation {agent.agent_id=} {invocation_id=}: {exc}")


class XMentionsProcessor:
    """
    X mentions processor.
    """

    def __init__(self):
        self.running = False
        self._runtime = time.time()
        self._tweet_batch = []
        self._last_indexed = time.time()
        self._index_lock = asyncio.Lock()
        self.client = TweepyClient(
            bearer_token=settings.tweepy_client.bearer_token, wait_on_rate_limit=True
        )
        self._last_processed_ids: Dict[str, str] = {}

    async def start(self):
        """
        Start processing.
        """
        if self.running:
            return
        self.running = True
        asyncio.create_task(self._check_mentions_loop())

    async def stop(self):
        """
        Stop processing.
        """
        self.running = False

    async def _get_active_agents(self) -> List[Agent]:
        """
        Load active agents with X integration configured.
        """
        async with get_session() as db:
            result = await db.execute(
                select(Agent)
                .where(Agent.x_username.isnot(None))
                .where(Agent.x_user_id.isnot(None))
                .where(Agent.x_access_token.isnot(None))
                .where(Agent.deleted_at.is_(None))
            )
            return result.unique().scalars().all()

    async def _index_tweet_batch(self, batch):
        """
        Index a batch of tweets async.
        """
        if not batch:
            return

        # Inject usernames from author ID.
        logger.info(f"Discovering usernames for batch of {len(batch)} users...")
        user_map = await get_users_by_id([tweet["author_id"] for tweet in batch])
        for tweet in batch:
            tweet["username"] = user_map.get(tweet["author_id"], "__unknown__")

        # Convert to index format, including generating embeddings, and index via bulk.
        logger.info(
            f"Generating index documents for {len(batch)} tweets and performing bulk index..."
        )
        auth = "Bearer " + generate_auth_token(settings.default_user_id, duration_minutes=5)
        try:
            tweets = await asyncio.gather(*[tweet_to_index_format(item, auth) for item in batch])
            if tweets:
                await index_tweets(tweets)
        except Exception as exc:
            logger.error(f"Error indexing tweet batch: {exc}\n{traceback.format_exc()}")

    async def inject_parent_tweets(self, tweet, max_depth=5):
        """
        Inject parent tweet objects up to recursion limit.
        """
        current_depth = 0
        parent_id = None
        if "referenced_tweets" in tweet and tweet["referenced_tweets"]:
            for ref in tweet["referenced_tweets"]:
                if ref["type"] == "replied_to":
                    parent_id = ref["id"]
                    break
        if not parent_id:
            return
        auth = "Bearer " + generate_auth_token(settings.default_user_id, duration_minutes=30)
        target_object = tweet
        while parent_id and current_depth < max_depth:
            logger.info(f"Attempting to inject parent tweet: {parent_id}")
            try:
                tweets = await get_and_index_tweets([parent_id], auth)
                if not tweets or len(tweets) == 0:
                    logger.warning(f"Failed to fetch tweet with {parent_id=}")
                    break
                parent = tweets[0]
                if parent:
                    logger.success(f"Fetched the parent tweet: {parent.model_dump()}")
                    target_object["referenced_tweet_parent"] = parent.model_dump()
                    current_depth += 1
                    target_object = target_object["referenced_tweet_parent"]
                    parent_id = None
                    if "referenced_tweets" in target_object and target_object["referenced_tweets"]:
                        for ref in target_object["referenced_tweets"]:
                            if ref["type"] == "replied_to":
                                parent_id = ref["id"]
                                break
                else:
                    break
            except Exception as e:
                logger.error(f"Error retrieving parent tweet: {e}")
                break

    async def _check_mentions_for_agent(self, agent: Agent):
        """
        Check mentions for a specific agent and process any new mentions.
        """
        if not agent.x_user_id:
            logger.warning(f"Agent {agent.agent_id} has no X user ID")
            return

        since_id = self._last_processed_ids.get(agent.x_user_id)
        start_time = None
        if not since_id and not agent.x_last_mentioned_at:
            start_time = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        elif not since_id and agent.x_last_mentioned_at:
            start_time = (agent.x_last_mentioned_at + timedelta(seconds=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        try:
            logger.info(f"Checking mentions for agent {agent.agent_id} ({agent.x_username})")
            mentions = self.client.get_users_mentions(
                id=agent.x_user_id,
                since_id=since_id,
                start_time=start_time,
                max_results=MAX_RESULTS,
                tweet_fields=TWEET_FIELDS,
                expansions=[
                    "author_id",
                    "entities.mentions.username",
                    "referenced_tweets.id",
                    "attachments.media_keys",
                    "in_reply_to_user_id",
                ],
                user_fields=["username"],
            )
            if not mentions.data:
                logger.info(f"No new mentions for agent {agent.agent_id} ({agent.x_username})")
                return
            logger.info(
                f"Found {len(mentions.data)} new mentions for agent {agent.agent_id} ({agent.x_username})"
            )

            latest_mention_id = None
            latest_mention_time = None
            sorted_mentions = sorted(mentions.data, key=lambda t: t.created_at)
            for tweet in sorted_mentions:
                tweet_dict = tweet.data
                if (
                    getattr(tweet, "referenced_tweets", None)
                    and "referenced_tweets" not in tweet_dict
                ):
                    tweet_dict["referenced_tweets"] = tweet.referenced_tweets
                latest_mention_id = tweet.id
                latest_mention_time = tweet.created_at
                async with self._index_lock:
                    self._tweet_batch.append(tweet_dict)
                    if len(self._tweet_batch) >= 100 or time.time() - self._last_indexed >= 60:
                        asyncio.create_task(self._index_tweet_batch(self._tweet_batch))
                        self._tweet_batch = []
                        self._last_indexed = time.time()

                # Check if the tweet passes the filter if one is set
                if agent.x_invoke_filter and agent.x_invoke_filter not in tweet.text:
                    logger.info(
                        f"Skipping tweet, missing filter {agent.x_username=} {agent.x_invoke_filter=}"
                    )
                    continue

                # Are we mentioning ourself?
                reply_to_user = str(getattr(tweet, "in_reply_to_user_id", ""))
                if str(tweet.author_id) == str(agent.x_user_id) and reply_to_user == str(
                    agent.x_user_id
                ):
                    continue

                # Inject parent hierarchy.
                await self.inject_parent_tweets(tweet_dict)

                # Check if we already have an interaction for this one.
                async with get_session() as session:
                    existing = (
                        (
                            await session.execute(
                                select(AgentXInteraction).where(
                                    AgentXInteraction.agent_id == agent.agent_id,
                                    AgentXInteraction.tweet_id == str(tweet.id),
                                )
                            )
                        )
                        .unique()
                        .scalar_one_or_none()
                    )
                    if existing:
                        logger.info(f"Already processed {tweet.id=}")
                        continue
                    session.add(
                        AgentXInteraction(
                            agent_id=agent.agent_id, tweet_id=str(tweet.id), created_at=func.now()
                        )
                    )
                    await session.commit()
                logger.info(
                    f"Processing mention: {tweet.id=} {tweet.author_id=} {agent.x_username=}"
                )
                if await rate_limit(f"agent_x_call:{agent.agent_id}", 10, 60):
                    logger.warning(f"Rate limit exceeded for {agent.agent_id=} {agent.x_username=}")
                    continue

                await _create_invocation(agent, tweet_dict)

            # Update the last processed ID for this agent
            if latest_mention_id:
                self._last_processed_ids[agent.x_user_id] = latest_mention_id

                # Update the last_mentioned_at timestamp in the database
                async with get_session() as session:
                    await session.execute(
                        update(Agent)
                        .where(Agent.agent_id == agent.agent_id)
                        .values(x_last_mentioned_at=latest_mention_time)
                    )
                    await session.commit()
                    logger.info(
                        f"Updated last_mentioned_at for agent {agent.agent_id} to {latest_mention_time}"
                    )
        except TweepyException as exc:
            logger.error(f"Error checking mentions for agent {agent.agent_id}: {exc}")
        except Exception as exc:
            logger.error(f"Unexpected error checking mentions: {exc}\n{traceback.format_exc()}")

    async def _check_mentions_loop(self):
        """
        Periodically check mentions for all configured agents.
        """
        while self.running:
            try:
                agents = await self._get_active_agents()
                logger.info(f"Checking mentions for {len(agents)} active agents")
                for agent in agents:
                    try:
                        await self._check_mentions_for_agent(agent)
                        await asyncio.sleep(5)
                    except Exception as exc:
                        logger.error(f"Error processing agent {agent.agent_id}: {exc}")
                async with self._index_lock:
                    if self._tweet_batch:
                        asyncio.create_task(self._index_tweet_batch(self._tweet_batch))
                        self._tweet_batch = []
                        self._last_indexed = time.time()
                logger.info("Mentions check complete, waiting 60 seconds before next check")
                await asyncio.sleep(60)
            except Exception as exc:
                logger.error(f"Error in mentions check loop: {exc}\n{traceback.format_exc()}")
                await asyncio.sleep(60)


async def main():
    """
    Main loop.
    """
    processor = XMentionsProcessor()
    await processor.start()

    try:
        while True:
            await asyncio.sleep(1)
            if not processor.running:
                break
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await processor.stop()


if __name__ == "__main__":
    asyncio.run(main())
