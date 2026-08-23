import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import Header, HTTPException

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "api_keys.db"

# Secret used to create/manage API keys (the /admin/keys endpoints).
# Set this as a real environment variable before deploying:
#   export ADMIN_SECRET="something-long-and-random"
# If it's left unset, admin endpoints are disabled rather than falling
# back to a guessable default.
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            key_hash TEXT UNIQUE NOT NULL,
            name TEXT,
            created_at REAL,
            requests INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    """)
    return conn


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(name: str = "") -> str:
    """Generate a new API key, store only its hash, return the plaintext
    key once (the caller must save it — it can't be recovered later)."""
    raw_key = "ck_" + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(8)
    conn = _get_db()
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, name, created_at) VALUES (?, ?, ?, ?)",
        (key_id, _hash(raw_key), name, time.time())
    )
    conn.commit()
    conn.close()
    return raw_key


def list_keys():
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, name, created_at, requests, active FROM api_keys"
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "requests": r[3], "active": bool(r[4])}
        for r in rows
    ]


def revoke_key(key_id: str) -> bool:
    conn = _get_db()
    cur = conn.execute("UPDATE api_keys SET active = 0 WHERE id = ?", (key_id,))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def require_admin(x_admin_secret: str = Header(default=None)):
    if not ADMIN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_SECRET орнотулган эмес — админ endpoint'тери өчүрүлгөн"
        )
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Уруксат жок")


def require_api_key(x_api_key: str = Header(default=None)):
    """FastAPI dependency — protects an endpoint behind a valid API key."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header керек")

    conn = _get_db()
    row = conn.execute(
        "SELECT id, active FROM api_keys WHERE key_hash = ?",
        (_hash(x_api_key),)
    ).fetchone()

    if not row or not row[1]:
        conn.close()
        raise HTTPException(status_code=401, detail="API key жараксыз же өчүрүлгөн")

    conn.execute("UPDATE api_keys SET requests = requests + 1 WHERE id = ?", (row[0],))
    conn.commit()
    conn.close()
    return row[0]
