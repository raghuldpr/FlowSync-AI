"""Signed device-API client shared by the simulator and the camera pipeline.

`client` is any httpx-style client (httpx.Client against a live backend, or FastAPI's TestClient).
"""
import json
import secrets

from .security import sign


class DeviceClient:
    def __init__(self, client, device_id: str, key: str):
        self.c, self.id, self.key = client, device_id, key
        self.boot, self.seq = secrets.token_hex(4), 0

    def request(self, method: str, path: str, obj=None, params=None):
        """`path` is what gets signed (the URL path only, as the server sees it); `params` is the query string."""
        self.seq += 1
        body = json.dumps(obj).encode() if obj is not None else b""
        headers = {"X-Device-Id": self.id, "X-Boot": self.boot, "X-Seq": str(self.seq),
                   "X-Signature": sign(self.key, method, path, self.id, self.boot, self.seq, body),
                   "Content-Type": "application/json"}
        kw = {"params": params} if params else {}
        return self.c.request(method, path, content=body, headers=headers, **kw)
