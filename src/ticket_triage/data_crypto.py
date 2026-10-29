"""可选静态加密：配置 DATA_AT_REST_KEY 时对 subject/body 用 Fernet。

未配置时明文落库，生产须磁盘/卷加密 + 传输 TLS（见 docs/GATEWAY.md、docs/DATA.md）。
"""

from __future__ import annotations

import base64
import hashlib
import logging

from ticket_triage.config import settings

logger = logging.getLogger("ticket_triage.crypto")
_PREFIX = "enc:v1:"


def _fernet():
    key = settings.data_at_rest_key
    if not key or not str(key).strip():
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning("cryptography not installed; data_at_rest_key ignored")
        return None
    raw = str(key).strip().encode()
    # 允许任意口令：派生成 urlsafe 32 字节 key
    digest = hashlib.sha256(raw).digest()
    fkey = base64.urlsafe_b64encode(digest)
    return Fernet(fkey)


def encryption_enabled() -> bool:
    return _fernet() is not None


def seal_text(plain: str | None) -> str | None:
    if plain is None:
        return None
    f = _fernet()
    if f is None:
        return plain
    token = f.encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def open_text(stored: str | None) -> str | None:
    if stored is None:
        return None
    if not stored.startswith(_PREFIX):
        return stored
    f = _fernet()
    if f is None:
        raise RuntimeError("encrypted_field_without_key")
    token = stored[len(_PREFIX) :].encode("ascii")
    return f.decrypt(token).decode("utf-8")
