"""Bound expensive work and isolate externally supplied map destinations."""
import asyncio
from collections import deque
from contextlib import contextmanager
import ipaddress
import socket
import time

import aiohttp
from aiohttp.abc import AbstractResolver
from yarl import URL


class HistoryBusy(Exception):
    """The caller should retry later instead of adding queued work."""


class Admission:
    """Non-waiting global/per-user concurrency and rolling-minute limits."""

    def __init__(self):
        self.active = 0
        self.users = {}
        self.recent = deque()

    @contextmanager
    def request(self, user_id):
        now = time.monotonic()
        while self.recent and self.recent[0] <= now - 60:
            self.recent.popleft()
        for key, (active, recent) in list(self.users.items()):
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if not active and not recent:
                del self.users[key]
        active, recent = self.users.get(user_id, (0, deque()))
        if (self.active >= 8 or active >= 2 or len(self.recent) >= 60
                or len(recent) >= 20 or (user_id not in self.users and len(self.users) >= 128)):
            raise HistoryBusy
        self.active += 1
        recent.append(now)
        self.recent.append(now)
        self.users[user_id] = (active + 1, recent)
        try:
            yield
        finally:
            self.active -= 1
            active, recent = self.users[user_id]
            self.users[user_id] = (active - 1, recent)


class WorkPool:
    """Share identical operations and bound work even after callers disconnect."""

    def __init__(self):
        self.tasks = {}
        self.closed = False

    async def run(self, key, factory):
        if self.closed:
            raise HistoryBusy
        task = self.tasks.get(key)
        if task is None:
            if len(self.tasks) >= 4:
                raise HistoryBusy

            async def bounded():
                async with asyncio.timeout(120):
                    return await factory()

            task = asyncio.create_task(bounded())
            self.tasks[key] = task

            def done(completed):
                self.tasks.pop(key, None)
                if not completed.cancelled():
                    completed.exception()  # Observe errors when all callers have left.

            task.add_done_callback(done)
        return await asyncio.shield(task)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def visible_records(result, setting_entities, user, policy):
    """Never mutate shared cached records while enforcing entity read policy."""
    denied = {
        field for field, entity_id in setting_entities.items()
        if not entity_id or not user.permissions.check_entity(entity_id, policy)
    }
    return {**result, 'records': [
        {key: value for key, value in record.items() if key not in denied}
        for record in result['records']
    ]}


def public_address(value):
    address = ipaddress.ip_address(value)
    # IPv4-mapped IPv6 must obey the same policy as the underlying IPv4 address.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_unspecified and not address.is_loopback
            and not address.is_link_local)


def map_url(value):
    """Use the HTTP client's URL parser and preserve the signed query verbatim."""
    url = URL(value, encoded=True)
    if (url.scheme != 'https' or not url.raw_host or url.port != 443
            or url.user is not None or url.password is not None or url.fragment):
        raise ValueError('Invalid map destination')
    try:
        address = ipaddress.ip_address(url.raw_host)
    except ValueError:
        pass  # Domain names are validated by the connector's resolver below.
    else:
        if not public_address(address):
            raise ValueError('Invalid map destination')
    return url


class PublicResolver(AbstractResolver):
    """Validate the exact DNS results consumed by the connector, without re-resolving."""

    def __init__(self):
        self.resolver = aiohttp.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await self.resolver.resolve(host, port, family)
        if not addresses or any(not public_address(item['host']) for item in addresses):
            raise OSError('Invalid map destination')
        return addresses

    async def close(self):
        await self.resolver.close()


async def download_map(value):
    """Credential-free HTTPS fetch; no proxies, cookies, or automatic redirects."""
    url = map_url(value)
    resolver = PublicResolver()
    try:
        async with asyncio.timeout(25):
            connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
            async with aiohttp.ClientSession(
                connector=connector, cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            ) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if 300 <= response.status < 400:
                        raise ValueError('Map redirects are not allowed')
                    response.raise_for_status()
                    chunks = []
                    total = 0
                    async for chunk in response.content.iter_chunked(65536):
                        total += len(chunk)
                        if total > 8_000_000:
                            raise ValueError('Map too large')
                        chunks.append(chunk)
                    return b''.join(chunks)
    finally:
        await resolver.close()
