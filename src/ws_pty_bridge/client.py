import argparse
import asyncio
import fcntl
import os
import signal
import struct
import sys
import termios
import tty

from websockets.asyncio.client import connect

from .protocol import MAX_MESSAGE, decode, encode, pack, unpack
from .server import secret


async def terminal(url: str, token: str):
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        raise RuntimeError("An interactive TTY is required")
    async with connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                       max_size=MAX_MESSAGE) as connection:
        if unpack(await connection.recv())["type"] != "ready":
            raise RuntimeError("Agent did not open a shell")
        old = termios.tcgetattr(fd)
        loop = asyncio.get_running_loop()
        incoming = asyncio.create_task(receive(connection))
        stop = loop.create_future()

        def read_stdin():
            data = os.read(fd, 16384)
            if data:
                asyncio.create_task(connection.send(pack("input", data=encode(data))))
            elif not stop.done():
                stop.set_result(None)

        def resize():
            rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
            asyncio.create_task(connection.send(pack("resize", rows=rows, cols=cols)))

        try:
            tty.setraw(fd)
            loop.add_reader(fd, read_stdin)
            loop.add_signal_handler(signal.SIGWINCH, resize)
            resize()
            await asyncio.wait({incoming, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            loop.remove_reader(fd)
            loop.remove_signal_handler(signal.SIGWINCH)
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            incoming.cancel()
            await asyncio.gather(incoming, return_exceptions=True)
            await connection.send(pack("close"))


async def receive(connection):
    async for raw in connection:
        message = unpack(raw)
        if message["type"] == "output":
            os.write(sys.stdout.fileno(), decode(message["data"]))
        elif message["type"] in ("exit", "error"):
            break


def main():
    parser = argparse.ArgumentParser(description="Open an interactive terminal on authorized B")
    parser.add_argument("url", help="wss://gateway.example/terminal/B")
    args = parser.parse_args()
    if not args.url.startswith("wss://") and not args.url.startswith("ws://127.0.0.1:"):
        parser.error("Use wss:// outside local loopback testing")
    try:
        asyncio.run(terminal(args.url, secret("WS_PTY_ADMIN_TOKEN")))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
