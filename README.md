# ws-pty-bridge

A minimal reverse WebSocket PTY for **servers you own or are authorized to administer**. B opens an outbound connection to public A; an authenticated administrator opens an interactive Bash session on B through A. One B and one session at a time. Linux and macOS (POSIX PTY); Python 3.11+.

## Install

On A, B and the administrator's computer:

```sh
python3 -m pip install .
```

Generate two different high-entropy secrets, for example `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` twice. Store them outside the repository, using environment variables supplied by your service manager. Never commit or paste secrets into shell history. A needs both, B needs only `WS_PTY_AGENT_TOKEN`, and the administrator needs only `WS_PTY_ADMIN_TOKEN`.

## Start

A listens on loopback only; terminate TLS with a trusted reverse proxy (example Caddy config below):

```sh
export WS_PTY_AGENT_TOKEN='your-unique-agent-secret-at-least-32-characters'
export WS_PTY_ADMIN_TOKEN='your-unique-admin-secret-at-least-32-characters'
ws-pty-server --host 127.0.0.1 --port 8765
```

Example Caddyfile (replace the domain with your own and configure DNS):

```caddyfile
terminal.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

On B, run under a dedicated, unprivileged OS account with only the permissions it needs:

```sh
export WS_PTY_AGENT_TOKEN='same-agent-secret-as-A'
ws-pty-agent wss://terminal.example.com/agent/B
```

On the administrator's terminal:

```sh
export WS_PTY_ADMIN_TOKEN='same-admin-secret-as-A'
ws-pty-client wss://terminal.example.com/terminal/B
```

The CLI requires a TTY; type `exit` to finish. B reconnects automatically if the network goes down. Each session creates a Bash process with the permissions of the B-side agent account.

## Status API

```sh
curl -H "Authorization: Bearer $WS_PTY_ADMIN_TOKEN" https://terminal.example.com/status
```

Returns JSON such as `{"agent_id":"B","online":true,"busy":false}`. Only WebSocket connections are supported for terminal operations; no unauthenticated command-execution HTTP endpoint is exposed.

## Security notes

- Use `wss://` with certificate validation in production. `ws://127.0.0.1` is allowed for local tests only; the A process refuses to bind publicly without a TLS proxy.
- Keep the agent and administrator tokens separate; rotate both if compromised. Restrict network access to A with a firewall or VPN where possible.
- The program does not record command content, terminal input, or output. Use host audit logging if your organization requires a durable session audit trail.
- This is a single-agent prototype, not an SSH replacement for production fleets. Treat access to the administrator token as equivalent to interactive shell access on B.

## Test

```sh
python3 -m pip install '.[test]'
python3 -m pytest -q
```
