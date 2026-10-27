"""Device authentication: allowlisted id + HMAC-SHA256 over method|path|id|boot|seq|body, replay rejection."""
import hashlib
import hmac
import threading


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


def sign(key: str, method: str, path: str, device_id: str, boot: str, seq: int, body: bytes) -> str:
    msg = f"{method.upper()}|{path}|{device_id}|{boot}|{seq}|".encode() + body
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


class DeviceAuthenticator:
    """seq must increase within a boot id; a boot id may never be reused once superseded.

    (ESP32s have no RTC, so a reboot picks a fresh random boot id and restarts seq at 1.)
    """

    def __init__(self, devices: dict):
        self.devices = devices
        self._lock = threading.Lock()
        self._current = {}      # device -> (boot, last_seq)
        self._old_boots = {}    # device -> set(boot)

    def verify(self, method, path, headers, body: bytes) -> tuple:
        device_id = headers.get("x-device-id", "")
        dev = self.devices.get(device_id)
        if dev is None:
            raise AuthError(403, "unknown_device")
        boot = headers.get("x-boot", "")
        try:
            seq = int(headers.get("x-seq", ""))
        except ValueError:
            raise AuthError(401, "bad_seq")
        if not boot or len(boot) > 64:
            raise AuthError(401, "bad_boot")
        expected = sign(dev["key"], method, path, device_id, boot, seq, body)
        if not hmac.compare_digest(expected, headers.get("x-signature", "")):
            raise AuthError(401, "bad_signature")
        with self._lock:
            cur = self._current.get(device_id)
            if cur and cur[0] == boot:
                if seq <= cur[1]:
                    raise AuthError(401, "replayed_seq")
            elif boot in self._old_boots.get(device_id, ()):
                raise AuthError(401, "replayed_boot")
            else:
                if cur:
                    self._old_boots.setdefault(device_id, set()).add(cur[0])
            self._current[device_id] = (boot, seq)
        return device_id, dev
