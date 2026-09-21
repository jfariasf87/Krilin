"""One bounded JSON request per connection over an ADB-forwarded loopback socket."""

from __future__ import annotations

import json
import socket
import time
from typing import Any

from .models import Action, KrilinError, Snapshot, StaleSnapshot

MAX_FRAME = 1_048_576


class BridgeDriver:
    def __init__(self, token: str, port: int = 8765) -> None:
        if len(token) < 32:
            raise ValueError("Bridge token must contain at least 32 characters")
        self.token = token
        self.port = port

    def _call(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        frame = json.dumps({"protocol": 1, "token": self.token, "timeout_ms": int(timeout * 1000), **payload}).encode() + b"\n"
        if len(frame) > MAX_FRAME:
            raise KrilinError("Bridge request exceeds size limit")
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=max(.001, timeout)) as conn:
                conn.sendall(frame)
                buf = bytearray()
                while b"\n" not in buf:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    conn.settimeout(remaining)
                    chunk = conn.recv(8192)
                    if not chunk:
                        raise KrilinError("Bridge disconnected; action outcome may be unknown")
                    buf.extend(chunk)
                    if len(buf) > MAX_FRAME:
                        raise KrilinError("Bridge response exceeds size limit")
            reply = json.loads(bytes(buf).split(b"\n", 1)[0])
            if not isinstance(reply, dict):
                raise KrilinError("Bridge response must be an object")
            if reply.get("error") == "stale_snapshot":
                raise StaleSnapshot("UI or input mode changed before execution")
            if reply.get("error") == "timeout_outcome_unknown" and payload.get("method") == "observe":
                raise KrilinError("Observation timed out; the app did not answer accessibility queries in time")
            if "error" in reply:
                raise KrilinError(f"Bridge rejected request: {reply['error']}")
            return reply
        except (OSError, ValueError) as exc:
            raise KrilinError(f"Bridge transport failed ({type(exc).__name__}); do not replay an uncertain action") from exc

    def observe(self, timeout: float = 5) -> Snapshot:
        return Snapshot.from_dict(self._call({"method": "observe"}, timeout))

    def execute(self, snapshot: Snapshot, action: Action, timeout: float = 5) -> None:
        reply = self._call({"method": "act", "snapshot_id": snapshot.id,
                            "kind": action.kind, "target": action.target, "text": action.text}, timeout)
        if reply.get("ok") is not True:
            raise KrilinError("Bridge did not acknowledge action")
