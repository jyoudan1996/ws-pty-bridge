import argparse
import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .protocol import MAX_MESSAGE, decode, encode, pack, unpack
from .server import secret


class Shell:
    def __init__(self, connection):
        self.connection = connection
        self.session_id: str | None = None
        self.pid: int | None = None
        self.fd: int | None = None
        self.reader: asyncio.Task | None = None

    async def start(self, session_id: str):
        self.session_id = session_id
        pid, fd = pty.fork()
        if pid == 0:
            os.execv("/bin/bash", ["/bin/bash", "-i"])
        self.pid, self.fd = pid, fd
        os.set_blocking(fd, False)
        self.reader = asyncio.create_task(self.read_output(session_id, fd, pid))
        await self.connection.send(pack("ready", id=session_id))

    async def read_output(self, session_id: str, fd: int, pid: int):
        loop = asyncio.get_running_loop()
        done = loop.create_future()

        def readable():
            try:
                data = os.read(fd, 16384)
                if data:
                    asyncio.create_task(self.connection.send(pack("output", id=session_id,
                                                                 data=encode(data))))
                else:
                    loop.remove_reader(fd)
                    done.set_result(None)
            except OSError:
                loop.remove_reader(fd)
                if not done.done():
                    done.set_result(None)

        loop.add_reader(fd, readable)
        try:
            await done
            await self.connection.send(pack("exit", id=session_id))
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            loop.remove_reader(fd)
            if self.pid == pid:
                await self.stop()

    async def stop(self):
        pid, fd = self.pid, self.fd
        self.pid, self.fd, self.session_id = None, None, None
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        if fd is not None:
            os.close(fd)
        await asyncio.to_thread(os.waitpid, pid, 0)

    async def handle(self, message: dict):
        kind = message["type"]
        if kind == "open" and self.pid is None:
            await self.start(message["id"])
        elif message.get("id") == self.session_id:
            if kind == "input":
                os.write(self.fd, decode(message["data"]))
            elif kind == "resize":
                rows, cols = int(message["rows"]), int(message["cols"])
                if 1 <= rows <= 1000 and 1 <= cols <= 1000:
                    fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            elif kind == "close":
                await self.stop()


async def run(url: str, token: str):
    delay = 1
    while True:
        try:
            async with connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                               max_size=MAX_MESSAGE, ping_interval=20, ping_timeout=20) as connection:
                delay = 1
                shell = Shell(connection)
                try:
                    async for raw in connection:
                        await shell.handle(unpack(raw))
                finally:
                    await shell.stop()
        except (OSError, ConnectionClosed) as exc:
            print(f"Agent connection lost: {exc}", flush=True)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


def main():
    parser = argparse.ArgumentParser(description="Connect authorized server B to gateway A")
    parser.add_argument("url", help="wss://gateway.example/agent/B (ws:// only for local testing)")
    args = parser.parse_args()
    if not args.url.startswith("wss://") and not args.url.startswith("ws://127.0.0.1:"):
        parser.error("Use wss:// outside local loopback testing")
    asyncio.run(run(args.url, secret("WS_PTY_AGENT_TOKEN")))


if __name__ == "__main__":
    main()
