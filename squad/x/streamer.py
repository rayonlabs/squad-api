import asyncio
import time
import json
import traceback
import squad.database.orms  # noqa
from loguru import logger
from tweepy import StreamRule
from typing import Set
from sqlalchemy import select, func
from tweepy.asynchronous import AsyncStreamingClient
from tweepy.errors import TweepyException
from squad.auth import generate_auth_token
from squad.config import settings
from squad.util import rate_limit, now_str
from squad.database import get_session
from squad.agent.schemas import Agent, get_by_x
from squad.invocation.schemas import get_unique_id, Invocation
from squad.storage.x import index_tweets, tweet_to_index_format, get_users_by_id

# Processing rate limit.
RATE_LIMIT = 100
RATE_LIMIT_WINDOW = 60

# Static rules.
STATIC_ACCOUNTS = " OR ".join(
    [
        "@rayon_labs",
        "@namoray_dev",
        "@jon_durbin",
        "@const_reborn",
        "@opentensor",
        "@shibshib89",
        "@mogmachine",
        "@0xcarro",
        "@WSquires",
        "@macrocrux",
        "@Old_Samster",
        "@EvanMalanga",
        "@RahulKumaran4",
        "@JosephJacks_",
        "@xponentcrisis",
        "@0xarrash",
        "@angad_ai",
        "@taostats",
        "@mogmachine",
        "@TAOTemplar",
        "@kenjonmiyachi",
        "@gylestensora",
        "@KeithSingery",
        "@brodyadreon",
        "@bittingthembits",
        "@evert_scott",
        "@badenglishtea",
        "@DreadBong0",
        "@TensorDetective",
        "@ai_bond_connery",
        "@brodydotai",
        "@VenturaLabs",
        "@tao_minersunion",
        "@21RoundTable",
    ]
)

STATIC_RULES = {
    f"(#bittensor OR $tao OR {STATIC_ACCOUNTS})"
    "-$fet -$FET -$eth -$ETH -$fart -$FART -$xrp -$XRP -$sol -$SOL -$trx -$TRX -$pepe -$PEPE -$aapl -$AAPL -telegram "
    "-$trump -$TRUMP -🔴LIVE -airdrop -#solana -#SOLANA -AIRDROP -Airdrop -$BTCAI -$btcai -is:retweet -is:reply",
}


async def _create_invocation(agent, tweet):
    """
    Create the invocation, which in turn will trigger the event.
    """
    task_text = "You have received the following tweet:\n" + json.dumps(tweet, indent=2)
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
            session.add(invocation)
            await session.commit()
            await session.refresh(invocation)
            await settings.redis_client.xadd(
                invocation.stream_key,
                {
                    "data": json.dumps(
                        {"log": "Queued agent call from X.", "timestamp": now_str()}
                    ).decode()
                },
            )
            logger.success(
                f"Successfully triggered invocation of {agent.agent_id=} {invocation_id=}"
            )
    except Exception as exc:
        logger.error(f"Failed to create invocation {agent.agent_id=} {invocation_id=}: {exc}")


class XR:
    """
    X stream processor.
    """

    MAX_RULES = 1000 - len(STATIC_RULES)

    def __init__(self):
        self.running = False
        self._runtime = time.time()
        self._tweet_batch = []
        self._last_indexed = time.time()
        self._index_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
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
        # Purge the stream rules to see if it clears out any stray connections on X side.
        try:
            current_rules = await self.stream.get_rules()
            all_rules = [rule.id for rule in current_rules.data]
            if all_rules:
                logger.warning(f"Purging {len(all_rules)} rules from stream...")
                await self.stream.delete_rules(all_rules)
        except Exception as exc:
            logger.error(f"Error resetting stream rules: {exc}")
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
        await self._safely_disconnect()

    async def _safely_disconnect(self):
        """
        Safely disconnect the stream if it exists.
        """
        if self.stream:
            try:
                self.stream.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting stream: {e}")
            finally:
                self.stream = None

    async def _get_active_usernames(self) -> Set[str]:
        """
        Load active agent usernames.
        """
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
        if not self.stream:
            logger.warning("Cannot sync rules: stream client is not initialized")
            return

        try:
            current_rules = await self.stream.get_rules()
            rule_map = {}
            for rule in current_rules.data or []:
                rule_map[rule.value] = rule.id
            current_rule_values = set(rule_map)
            db_usernames = await self._get_active_usernames()
            rules_to_add = ({f"@{u}" for u in db_usernames} | STATIC_RULES) - current_rule_values
            rules_to_remove = current_rule_values - ({f"@{u}" for u in db_usernames} | STATIC_RULES)
            if rules_to_remove:
                await self.stream.delete_rules([rule_map[rule] for rule in rules_to_remove])
            if rules_to_add:
                await self.stream.add_rules([StreamRule(rule) for rule in rules_to_add])
            self._runtime = time.time()
        except TweepyException as e:
            logger.error(f"Error syncing X rules, keeping as-is for now: {e}")

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

    async def _start_stream(self):
        """
        Listen for stream events.
        """

        async def on_tweet(tweet_obj):
            # Make sure we're not getting overwhelmed.
            if await rate_limit("x:all_stream", RATE_LIMIT, RATE_LIMIT_WINDOW, incr_by=0):
                logger.warning("Getting overwhelmed from X stream, backing off...")
                return
            await rate_limit("x:all_stream", RATE_LIMIT, RATE_LIMIT_WINDOW)

            # Always index the tweets, whether they are useful triggers or not.
            tweet = tweet_obj.data
            async with self._index_lock:
                cashtags = tweet.get("entities", {}).get("cashtags", [])
                if len(cashtags) <= 3:
                    self._tweet_batch.append(tweet)
                    if len(self._tweet_batch) >= 100 or time.time() - self._last_indexed >= 60:
                        asyncio.create_task(self._index_tweet_batch(self._tweet_batch))
                        self._tweet_batch = []
                        self._last_indexed = time.time()

            mentions = tweet.get("entities", {}).get("mentions", [])
            for mention in mentions:
                username = mention.get("username") if isinstance(mention, dict) else mention
                if not username:
                    continue
                agent = await get_by_x(username, runtime=self._runtime)
                if agent:
                    if agent.x_invoke_filter and agent.x_invoke_filter not in tweet["text"]:
                        logger.info(
                            f"Skipping tweet, missing filter {username=} {agent.x_invoke_filter=}"
                        )
                        continue
                    logger.info(f"Received tweet: {tweet['id']=} {tweet['author_id']=} {username=}")
                    if await rate_limit(f"agent_x_call:{agent.agent_id}", 10, 60):
                        logger.warning(f"Rate limit exceeded for {agent.agent_id=} {username=}")
                        continue
                    await _create_invocation(agent, tweet)

        async def on_error(error):
            logger.error(f"X stream error: {error}")
            if not self.running:
                return
            backoff_time = 30
            max_retries = 5
            retries = 0
            await self._safely_disconnect()
            self.stream = None
            while self.running and retries < max_retries:
                logger.info(
                    f"Attempting to reconnect in {backoff_time} seconds (attempt {retries+1}/{max_retries})"
                )
                await asyncio.sleep(backoff_time)
                try:
                    self.stream = AsyncStreamingClient(
                        bearer_token=settings.tweepy_client.bearer_token,
                        wait_on_rate_limit=True,
                    )
                    self.stream.on_tweet = on_tweet
                    self.stream.on_error = on_error
                    logger.info("New client created. Syncing rules...")

                    # Purge the stream rules to see if it clears out any stray connections on X side.
                    try:
                        current_rules = await self.stream.get_rules()
                        all_rules = [rule.id for rule in current_rules.data]
                        if all_rules:
                            logger.warning(f"Purging {len(all_rules)} rules from stream...")
                            await self.stream.delete_rules(all_rules)
                    except Exception as exc:
                        logger.error(f"Error resetting stream rules: {exc}")
                    await self._sync_rules()
                    logger.info("Rules synced. Starting filter...")
                    await self.stream.filter(
                        tweet_fields=[
                            "author_id",
                            "entities",
                            "public_metrics",
                            "attachments",
                        ]
                    )
                    logger.info("Successfully reconnected to X stream")
                    self.stream.on_tweet = on_tweet
                    self.stream.on_error = on_error
                    self.stream.on_request_error = on_error
                    return
                except Exception as exc:
                    logger.error(f"Failed to reconnect during attempt {retries+1}: {exc}")
                    retries += 1
                    backoff_time = min(300, backoff_time * 2)

            if retries >= max_retries:
                logger.critical(
                    "Failed to reconnect after maximum retries, stopping X stream service"
                )
                self.running = False

        if not self.stream:
            logger.error("Cannot start stream: stream client is not initialized")
            return

        self.stream.on_tweet = on_tweet
        self.stream.on_error = on_error
        self.stream.on_request_error = on_error
        try:
            await self.stream.filter(
                tweet_fields=[
                    "author_id",
                    "entities",
                    "public_metrics",
                    "attachments",
                ]
            )
        except Exception as e:
            logger.error(f"Error starting stream: {e}")


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
