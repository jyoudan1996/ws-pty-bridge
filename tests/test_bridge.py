import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from ws_pty_bridge.agent import Shell
from ws_pty_bridge.protocol import encode, pack, unpack
from ws_pty_bridge.server import Gateway


@pytest.fixture
async def gateway_url():
    gateway = Gateway("a" * 40, "b" * 40)
    async with serve(gateway.handle, "127.0.0.1", 0, process_request=gateway.authorize) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", gateway


@pytest.mark.asyncio
async def test_auth_and_status(gateway_url):
    url, gateway = gateway_url
    with pytest.raises(InvalidStatus) as error:
        async with connect(f"{url}/terminal/B"):
            pass
    assert error.value.response.status_code == 401
    with pytest.raises(InvalidStatus) as status:
        async with connect(f"{url}/status", additional_headers={"Authorization": "Bearer " + "b" * 40}):
            pass
    assert status.value.response.status_code == 200


@pytest.mark.asyncio
async def test_interactive_shell_and_cleanup(gateway_url):
    url, gateway = gateway_url
    async with connect(f"{url}/agent/B", additional_headers={"Authorization": "Bearer " + "a" * 40}) as agent:
        shell = Shell(agent)

        async def agent_loop():
            async for raw in agent:
                await shell.handle(unpack(raw))

        task = asyncio.create_task(agent_loop())
        try:
            async with connect(f"{url}/terminal/B", additional_headers={"Authorization": "Bearer " + "b" * 40}) as client:
                assert unpack(await asyncio.wait_for(client.recv(), 5))["type"] == "ready"
                with pytest.raises(InvalidStatus) as duplicate:
                    async with connect(f"{url}/terminal/B", additional_headers={"Authorization": "Bearer " + "b" * 40}):
                        pass
                assert duplicate.value.response.status_code == 409
                await client.send(pack("resize", rows=40, cols=100))
                await client.send(pack("input", data=encode(b"printf 'bridge-ok\\n'; stty size; exit\n")))
                output = ""
                while "40 100" not in output:
                    message = unpack(await asyncio.wait_for(client.recv(), 5))
                    if message["type"] == "output":
                        from ws_pty_bridge.protocol import decode
                        output += decode(message["data"]).decode(errors="replace")
                assert "bridge-ok" in output
            await asyncio.sleep(0.1)
            assert gateway.session is None
            assert shell.pid is None
        finally:
            await agent.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await shell.stop()
