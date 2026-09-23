"""URL, DNS, domain pacing, and optional robots checks."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..errors import BrowsrError


class Guard:
    def __init__(self, cfg, limiter=None, user_agent: str = "browsr"):
        self.cfg = cfg
        if limiter is None:
            from ..ratelimit import RateLimiter

            limiter = RateLimiter()
        self.limiter = limiter
        self.user_agent = user_agent
        self._dns: dict[str, tuple[float, bool]] = {}
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _parts(url: str):
        try:
            parts = urlsplit(url)
            if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
                raise ValueError("invalid scheme or host")
            if parts.username is not None or parts.password is not None:
                raise ValueError("URL credentials are not supported")
            _ = parts.port
            return parts
        except (TypeError, ValueError) as exc:
            raise BrowsrError("bad_input", detail=str(exc)) from exc

    async def check_url(self, url: str) -> None:
        self._parts(url)
        if not await self.host_allowed(url):
            raise BrowsrError("forbidden_target")

    async def host_allowed(self, url: str) -> bool:
        try:
            parts = self._parts(url)
        except BrowsrError:
            return False
        if self.cfg.security.allow_private:
            return True
        host = parts.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return False
        cached = self._dns.get(host)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except (OSError, UnicodeError):
            # The actual fetch will surface an unknown-host error.
            return True
        allowed = bool(infos)
        for info in infos:
            try:
                addr = ipaddress.ip_address(info[4][0].split("%", 1)[0])
            except ValueError:
                allowed = False
                break
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
                addr = addr.ipv4_mapped
            if not addr.is_global or any(
                (
                    addr.is_private,
                    addr.is_loopback,
                    addr.is_link_local,
                    addr.is_reserved,
                    addr.is_multicast,
                    addr.is_unspecified,
                )
            ):
                allowed = False
                break
        self._dns[host] = (now + 300, allowed)
        return allowed

    async def before_fetch(self, url: str) -> None:
        await self.check_url(url)
        parts = self._parts(url)
        host = parts.hostname.lower()
        domain = host.removeprefix("www.")
        if not await self.limiter.acquire(
            "domain:" + domain, self.cfg.security.domain_interval_s, 10
        ):
            raise BrowsrError("blocked")
        if self.cfg.security.respect_robots:
            origin = f"{parts.scheme}://{parts.netloc}"
            parser = await self._robots_parser(origin)
            if parser is not None and not parser.can_fetch("browsr", url):
                raise BrowsrError("disallowed")

    async def _robots_parser(self, origin: str) -> RobotFileParser | None:
        now = time.monotonic()
        cached = self._robots.get(origin)
        if cached is not None and cached[0] > now:
            return cached[1]
        if self._client is None:

            async def checked_request(request: httpx.Request) -> None:
                await self.check_url(str(request.url))

            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=5,
                headers={"User-Agent": self.user_agent},
                event_hooks={"request": [checked_request]},
            )
        parser = None
        try:
            response = await self._client.get(origin + "/robots.txt")
            if response.status_code < 400 and len(response.content) <= 1_000_000:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
        except (httpx.HTTPError, BrowsrError):
            pass
        self._robots[origin] = (now + 86400, parser)
        return parser

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
