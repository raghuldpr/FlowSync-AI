"""Operator accounts and bearer-token sessions (stdlib scrypt; no SSO/OAuth by design)."""
import hashlib
import hmac
import secrets
import time

from .db import Database, now_iso

N, R, P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=N, r=R, p=P)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def seed_operator(db: Database, password: str, username: str = "operator"):
    if db.one("SELECT 1 FROM users WHERE username=?", (username,)) is None:
        db.execute("INSERT INTO users VALUES(?,?,?,?)", (username, "operator", hash_password(password), now_iso()))


def login(db: Database, username: str, password: str, ttl_s: int):
    u = db.one("SELECT * FROM users WHERE username=?", (username,))
    if u is None:
        verify_password(password, "scrypt$00$00")        # keep timing similar
        return None
    if not verify_password(password, u["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), u["username"], u["role"], now_iso(), time.time() + ttl_s))
    return token


def resolve(db: Database, token: str):
    row = db.one("SELECT * FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    if row is None or row["expires_at"] < time.time():
        return None
    return {"username": row["username"], "role": row["role"]}
