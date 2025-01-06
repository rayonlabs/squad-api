"""
Simple aiohttp session manager.
"""

import asyncio
import aiohttp
from contextlib import asynccontextmanager


class SessionManager:
    def __init__(
        self, base_url: str = None, limit: int = 100, ttl_dns_cache: int = 300, headers: dict = {}
    ):
        self._session = None
        self._base_url = base_url
        self._limit = limit
        self._ttl_dns_cache = ttl_dns_cache
        self._headers = headers
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get or create an aiohttp session.
        """
        async with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    base_url=self._base_url,
                    connector=aiohttp.TCPConnector(
                        limit=self._limit, ttl_dns_cache=self._ttl_dns_cache, force_close=False
                    ),
                    headers=self._headers,
                    raise_for_status=True,
                )
            yield self._session

    async def close(self):
        """
        Close the session.
        """
        async with self._lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
