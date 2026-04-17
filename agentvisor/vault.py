import base64
import os
import sqlite3
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AGENTVISOR_DIR = Path.home() / ".agentvisor"
VAULT_DB = AGENTVISOR_DIR / "vault.db"
VAULT_KEY_FILE = AGENTVISOR_DIR / "vault.key"
KEYRING_SERVICE = "agentvisor"
KEYRING_USERNAME = "vault_key"


def _ensure_dir() -> None:
    AGENTVISOR_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


def _load_key() -> bytes:
    try:
        import keyring

        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if stored:
            return base64.b64decode(stored)
        key = os.urandom(32)
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, base64.b64encode(key).decode())
        return key
    except Exception:
        pass

    _ensure_dir()
    if VAULT_KEY_FILE.exists():
        return VAULT_KEY_FILE.read_bytes()

    print(
        f"WARNING: OS keychain unavailable. Vault key stored in {VAULT_KEY_FILE} (0600). "
        "Secure this file.",
        file=sys.stderr,
    )
    key = os.urandom(32)
    VAULT_KEY_FILE.write_bytes(key)
    VAULT_KEY_FILE.chmod(0o600)
    return key


def _get_conn() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(VAULT_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS credentials "
        "(service TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)"
    )
    conn.commit()
    return conn


def _encrypt(key: bytes, plaintext: str) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return nonce + ct


def _decrypt(key: bytes, blob: bytes) -> str:
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def store(service: str, secret: str, overwrite: bool = False) -> None:
    key = _load_key()
    blob = _encrypt(key, secret)
    conn = _get_conn()
    try:
        if overwrite:
            conn.execute(
                "INSERT OR REPLACE INTO credentials (service, ciphertext) VALUES (?, ?)",
                (service, blob),
            )
        else:
            try:
                conn.execute(
                    "INSERT INTO credentials (service, ciphertext) VALUES (?, ?)",
                    (service, blob),
                )
            except sqlite3.IntegrityError:
                raise ValueError(
                    f"Credential '{service}' already exists. Use --overwrite to update."
                )
        conn.commit()
    finally:
        conn.close()


def get(service: str) -> str:
    key = _load_key()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT ciphertext FROM credentials WHERE service = ?", (service,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"No credential stored for '{service}'. Run: agentvisor store {service}")
    return _decrypt(key, row[0])


def list_credentials() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT service FROM credentials ORDER BY service"
        ).fetchall()
    finally:
        conn.close()
    return [{"service": row[0]} for row in rows]


def revoke(service: str) -> None:
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM credentials WHERE service = ?", (service,))
        conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"No credential found for '{service}'")
    finally:
        conn.close()
