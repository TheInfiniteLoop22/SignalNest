"""Raw-socket faults that HTTP frameworks can't express.

Each helper returns a running asyncio server; the port is `server.sockets[0].getsockname()[1]`.
"""

import asyncio
import socket
import ssl
import struct


async def start_rst_server(host: str = "127.0.0.1") -> asyncio.Server:
    """Accept, then reset the connection (SO_LINGER 0 turns close() into RST)."""

    async def handle(_r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        sock = w.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        w.close()

    return await asyncio.start_server(handle, host, 0)


async def start_close_server(host: str = "127.0.0.1") -> asyncio.Server:
    """Accept, read the request, then close cleanly without responding."""

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await r.readuntil(b"\r\n\r\n")
        w.close()

    return await asyncio.start_server(handle, host, 0)


async def start_tls_server(ctx: ssl.SSLContext, host: str = "127.0.0.1") -> asyncio.Server:
    """A TLS server answering 200; pair with an untrusted/wrong-name cert for TLS faults."""

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            await r.readuntil(b"\r\n\r\n")
            w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await w.drain()
        finally:
            w.close()

    return await asyncio.start_server(handle, host, 0, ssl=ctx)


def free_port(host: str = "127.0.0.1") -> int:
    """A port with nothing listening (briefly bound, then released)."""
    with socket.socket() as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])
