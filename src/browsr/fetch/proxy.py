"""Small local browser proxy that guards requests missed by Playwright routes.

Firefox does not invoke route handlers for each hop of an HTTP redirect. The
browser therefore uses this proxy for every connection. CONNECT is inspected
before a TCP socket is opened, and DNS is resolved again for the actual socket
so a changed address cannot bypass the check.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from ..errors import BrowsrError


class GuardProxy:
    def __init__(self, cfg, guard):
        self.cfg = cfg
        self.guard = guard
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def server_url(self) -> str:
        if self._server is None:
            raise RuntimeError("proxy is not started")
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in tuple(self._writers):
            writer.close()
        if self._writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in tuple(self._writers)),
                return_exceptions=True,
            )

    async def _connect(self, host: str, port: int):
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not infos:
            raise OSError("no addresses")
        if not self.cfg.security.allow_private:
            for info in infos:
                address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                    address = address.ipv4_mapped
                if not address.is_global or any(
                    (
                        address.is_private,
                        address.is_loopback,
                        address.is_link_local,
                        address.is_reserved,
                        address.is_multicast,
                        address.is_unspecified,
                    )
                ):
                    raise BrowsrError("forbidden_target")
        last_error = None
        for info in infos:
            try:
                return await asyncio.open_connection(info[4][0], port, family=info[0])
            except OSError as exc:
                last_error = exc
        raise last_error or OSError("connection failed")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._writers.add(writer)
        remote_writer = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
            if len(head) > 65536:
                raise ValueError("request headers too large")
            first, _, rest = head.partition(b"\r\n")
            method, target, version = first.decode("latin-1").split(" ", 2)
            if method.upper() == "CONNECT":
                parts = urlsplit("https://" + target)
                if not parts.hostname or not parts.port:
                    raise ValueError("invalid CONNECT target")
                await self.guard.check_url(f"https://{target}/")
                remote_reader, remote_writer = await self._connect(parts.hostname, parts.port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await asyncio.gather(
                    self._pipe(reader, remote_writer),
                    self._pipe(remote_reader, writer),
                )
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    raise ValueError("invalid proxy target")
                await self.guard.check_url(target)
                port = parts.port or 80
                remote_reader, remote_writer = await self._connect(parts.hostname, port)
                origin_path = parts.path or "/"
                if parts.query:
                    origin_path += "?" + parts.query
                headers = [line for line in rest.split(b"\r\n") if line]
                headers = [
                    line
                    for line in headers
                    if line.split(b":", 1)[0].lower() not in {b"connection", b"proxy-connection"}
                ]
                remote_writer.write(
                    f"{method} {origin_path} {version}\r\n".encode("latin-1")
                    + b"\r\n".join(headers)
                    + b"\r\nConnection: close\r\n\r\n"
                )
                await remote_writer.drain()
                await self._copy_request_body(reader, remote_writer, headers)
                await self._pipe(remote_reader, writer)
        except (TimeoutError, asyncio.IncompleteReadError, ValueError, OSError, BrowsrError):
            if remote_writer is None:
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            if remote_writer is not None:
                remote_writer.close()
                await remote_writer.wait_closed()
            writer.close()
            await writer.wait_closed()
            self._writers.discard(writer)

    @staticmethod
    async def _copy_request_body(reader, writer, headers: list[bytes]) -> None:
        values = {}
        for line in headers:
            if b":" in line:
                key, value = line.split(b":", 1)
                values[key.lower()] = value.strip().lower()
        if b"chunked" in values.get(b"transfer-encoding", b""):
            while True:
                line = await reader.readline()
                size = int(line.split(b";", 1)[0], 16)
                writer.write(line)
                if size == 0:
                    while trailer := await reader.readline():
                        writer.write(trailer)
                        if trailer == b"\r\n":
                            break
                    await writer.drain()
                    return
                writer.write(await reader.readexactly(size + 2))
                await writer.drain()
        else:
            length = int(values.get(b"content-length", b"0"))
            while length:
                chunk = await reader.readexactly(min(length, 65536))
                writer.write(chunk)
                await writer.drain()
                length -= len(chunk)

    @staticmethod
    async def _pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        try:
            while chunk := await source.read(65536):
                target.write(chunk)
                await target.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                target.write_eof()
            except (OSError, RuntimeError):
                pass
