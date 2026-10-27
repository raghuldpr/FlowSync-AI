"""Safe storage for road-camera uploads: size cap, JPEG magic bytes, server-generated names (never the client's), retention."""
import os
import time
import uuid
from pathlib import Path

MAX_BYTES = 400_000
KEEP_S = 24 * 3600


class ImageError(ValueError):
    pass


def validate(data: bytes):
    if not data:
        raise ImageError("empty image")
    if len(data) > MAX_BYTES:
        raise ImageError("image too large")
    if data[:3] != b"\xff\xd8\xff":
        raise ImageError("not a JPEG")
    if data[-2:] != b"\xff\xd9":
        raise ImageError("truncated JPEG")


def store(data: bytes, root: str) -> tuple:
    """Validates and writes the image. Returns (image_id, path). The id is a random UUID: path traversal is impossible."""
    validate(data)
    Path(root).mkdir(parents=True, exist_ok=True)
    image_id = uuid.uuid4().hex
    path = Path(root) / f"{image_id}.jpg"
    path.write_bytes(data)
    return image_id, str(path)


def purge_old(db, root: str, now: float = None) -> int:
    """Delete images (file + row) older than KEEP_S. Returns how many."""
    from datetime import datetime, timezone
    cutoff = datetime.fromtimestamp((now or time.time()) - KEEP_S, timezone.utc).isoformat(timespec="milliseconds")
    n = 0
    for r in db.query("SELECT id, path FROM images WHERE created_at < ?", (cutoff,)):
        try:
            os.remove(r["path"])
        except OSError:
            pass
        db.execute("DELETE FROM images WHERE id=?", (r["id"],))
        n += 1
    return n
