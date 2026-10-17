import argparse
import asyncio
import hmac
import json
import os
from dataclasses import dataclass
from http import HTTPStatus
from uuid import uuid4

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .protocol import MAX_MESSAGE, pack, unpack


def secret(name: str) -> str:
    value = os.environ.get(name)
    if not value or len(value) < 32:
        raise ValueError(f"{name} must contain at least 32 characters")
    return value


@dataclass
class Session:
    id: str
    terminal: ServerConnection
    ready: asyncio.Future[None]


class Gateway:
    def __init__(self, agent_token: str, admin_token: str, agent_id: str = "B"):
        self.agent_token = agent_token
        self.admin_token = admin_token
        self.agent_id = agent_id
        self.agent: ServerConnection | None = None
        self.session: Session | None = None

    async def authorize(self, connection: ServerConnection, request):
        path = request.path.split("?", 1)[0]
        agent_path = f"/agent/{self.agent_id}"
        terminal_path = f"/terminal/{self.agent_id}"
        if path not in (agent_path, terminal_path, "/status"):
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        expected = self.agent_token if path == agent_path else self.admin_token
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization, f"Bearer {expected}"):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        if path == "/status":
            body = json.dumps({"agent_id": self.agent_id, "online": self.agent is not None,
                               "busy": self.session is not None}).encode()
            response = connection.respond(HTTPStatus.OK, body.decode())
            response.headers["Content-Type"] = "application/json"
            response.body = body
            return response
        if path == agent_path and self.agent is not None:
            return connection.respond(HTTPStatus.CONFLICT, "Agent already connected\n")
        if path == terminal_path and (self.agent is None or self.session is not None):
            return connection.respond(HTTPStatus.CONFLICT, "Agent offline or busy\n")
        return None

    async def handle(self, connection: ServerConnection):
        path = connection.request.path.split("?", 1)[0]
        if path == f"/agent/{self.agent_id}":
            await self.handle_agent(connection)
        else:
            await self.handle_terminal(connection)

    async def handle_agent(self, connection: ServerConnection):
        if self.agent is not None:
            await connection.close(code=1008, reason="Agent already connected")
            return
        self.agent = connection
        try:
            async for raw in connection:
                message = unpack(raw)
                session = self.session
                if session is None or message.get("id") != session.id:
                    continue
                if message["type"] == "ready":
                    if not session.ready.done():
                        session.ready.set_result(None)
                elif message["type"] in ("output", "exit"):
                    await session.terminal.send(raw)
                    if message["type"] == "exit":
                        await session.terminal.close()
                elif message["type"] == "error":
                    if not session.ready.done():
                        session.ready.set_exception(RuntimeError(str(message.get("message", "Agent error"))))
                    else:
                        await session.terminal.send(raw)
                        await session.terminal.close()
        except (ConnectionClosed, ValueError, json.JSONDecodeError):
            pass
        finally:
            self.agent = None
            if self.session:
                await self.session.terminal.close(code=1011, reason="Agent disconnected")

    async def handle_terminal(self, connection: ServerConnection):
        if self.agent is None or self.session is not None:
            await connection.close(code=1008, reason="Agent offline or busy")
            return
        session = Session(uuid4().hex, connection, asyncio.get_running_loop().create_future())
        self.session = session
        try:
            await self.agent.send(pack("open", id=session.id))
            await asyncio.wait_for(session.ready, timeout=10)
            await connection.send(pack("ready", id=session.id))
            async for raw in connection:
                message = unpack(raw)
                if message.get("type") in ("input", "resize", "close"):
                    await self.agent.send(pack(message["type"], id=session.id,
                                               **{k: v for k, v in message.items()
                                                  if k not in ("type", "id")}))
                if message.get("type") == "close":
                    break
        except (ConnectionClosed, ValueError, json.JSONDecodeError, RuntimeError, TimeoutError):
            pass
        finally:
            if not session.ready.done():
                session.ready.cancel()
            if self.agent is not None:
                try:
                    await self.agent.send(pack("close", id=session.id))
                except ConnectionClosed:
                    pass
            if self.session is session:
                self.session = None
            await connection.close()


async def run(host: str, port: int, gateway: Gateway):
    async with serve(gateway.handle, host, port, process_request=gateway.authorize,
                     max_size=MAX_MESSAGE, ping_interval=20, ping_timeout=20) as server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Run the authenticated WebSocket terminal gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--agent-id", default="B")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        parser.error("Bind only to loopback; expose through a TLS reverse proxy")
    gateway = Gateway(secret("WS_PTY_AGENT_TOKEN"), secret("WS_PTY_ADMIN_TOKEN"), args.agent_id)
    asyncio.run(run(args.host, args.port, gateway))


if __name__ == "__main__":
    main()
