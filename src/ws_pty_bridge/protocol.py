import base64
import json

MAX_MESSAGE = 65536


def pack(kind: str, **fields: object) -> str:
    return json.dumps({"type": kind, **fields}, separators=(",", ":"))


def unpack(raw: str | bytes) -> dict:
    message = json.loads(raw)
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("invalid protocol message")
    return message


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)
