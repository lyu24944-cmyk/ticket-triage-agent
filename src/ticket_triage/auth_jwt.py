"""审核人员 JWT。

本地 / CI：HS256 + POST /auth/review/token（mint）。
内测 / 生产：配置 REVIEW_OIDC_* 后验签 IdP 令牌（JWKS RS256），mint 关闭。
验签通过后身份一律取 sub；租户取 tid / tenant_id / 配置的 claim。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

from ticket_triage.config import settings
from ticket_triage.models import clean_reviewer

_JWKS_LOCK = threading.Lock()
_JWKS_CACHE: dict[str, Any] = {"url": None, "fetched_at": 0.0, "keys": {}}


class ReviewTokenError(Exception):
    """令牌无效或过期。"""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def oidc_enabled() -> bool:
    issuer = settings.review_oidc_issuer
    return isinstance(issuer, str) and bool(issuer.strip())


def review_auth_mode() -> str:
    """review 鉴权模式：oidc | mint。"""
    return "oidc" if oidc_enabled() else "mint"


def review_jwt_secret() -> str | None:
    secret = settings.review_jwt_secret
    if isinstance(secret, str) and secret.strip():
        return secret.strip()
    # 未单独配置时，用审核引导密钥派生，避免本地再多记一把；生产应显式设 REVIEW_JWT_SECRET。
    from ticket_triage.secrets import review_api_keys

    keys = review_api_keys()
    if not keys:
        return None
    digest = hashlib.sha256(f"ticket-triage-review-jwt:{keys[0]}".encode()).hexdigest()
    return digest


@dataclass(frozen=True)
class ReviewPrincipal:
    """人员身份：来自 JWT sub，不是请求体字符串。"""

    subject: str
    token_id: str | None = None
    expires_at: int | None = None
    tenant_id: str = "default"


def mint_review_token(
    subject: str,
    *,
    ttl_seconds: int | None = None,
    now: int | None = None,
    tenant_id: str | None = None,
) -> str:
    """签发审核 JWT。OIDC 开启时不应再走此路径。"""
    from ticket_triage.tenants import DEFAULT_TENANT_ID

    if oidc_enabled():
        raise ReviewTokenError("review_mint_disabled")
    secret = review_jwt_secret()
    if not secret:
        raise ReviewTokenError("review_jwt_not_configured")
    sub = clean_reviewer(subject)
    tid = (tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.review_jwt_ttl_seconds)
    ttl = max(60, min(ttl, 24 * 3600))
    issued = int(now if now is not None else time.time())
    payload = {
        "sub": sub,
        "tid": tid,
        "iss": settings.app_name,
        "aud": "ticket-triage-review",
        "iat": issued,
        "exp": issued + ttl,
        "jti": _b64url_encode(hashlib.sha256(f"{sub}:{tid}:{issued}:{ttl}".encode()).digest()[:12]),
    }
    return _encode(payload, secret)


def verify_review_token(token: str, *, now: int | None = None) -> ReviewPrincipal:
    """验签人员令牌。OIDC 开启时只接受 IdP 令牌；否则接受本地 HS256。"""
    if oidc_enabled():
        return _verify_oidc_token(token, now=now)
    return _verify_hs256_token(token, now=now)


def _verify_hs256_token(token: str, *, now: int | None = None) -> ReviewPrincipal:
    from ticket_triage.tenants import DEFAULT_TENANT_ID

    secret = review_jwt_secret()
    if not secret:
        raise ReviewTokenError("review_jwt_not_configured")
    payload = _decode_hs256(token, secret, now=now)
    if payload.get("aud") != "ticket-triage-review":
        raise ReviewTokenError("review_token_aud")
    try:
        sub = clean_reviewer(str(payload.get("sub") or ""))
    except ValueError as exc:
        raise ReviewTokenError("review_token_sub") from exc
    tid_raw = payload.get("tid") or payload.get("tenant_id") or DEFAULT_TENANT_ID
    tid = str(tid_raw).strip() or DEFAULT_TENANT_ID
    return ReviewPrincipal(
        subject=sub,
        token_id=str(payload["jti"]) if payload.get("jti") else None,
        expires_at=int(payload["exp"]) if payload.get("exp") is not None else None,
        tenant_id=tid,
    )


def _oidc_jwks_url() -> str:
    explicit = settings.review_oidc_jwks_url
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    issuer = (settings.review_oidc_issuer or "").rstrip("/")
    return f"{issuer}/.well-known/jwks.json"


def _load_jwks(*, force: bool = False) -> dict[str, dict[str, Any]]:
    url = _oidc_jwks_url()
    ttl = max(60, int(settings.review_oidc_jwks_cache_seconds))
    with _JWKS_LOCK:
        cached_url = _JWKS_CACHE.get("url")
        age = time.time() - float(_JWKS_CACHE.get("fetched_at") or 0)
        keys = _JWKS_CACHE.get("keys") or {}
        if not force and cached_url == url and keys and age < ttl:
            return dict(keys)
    try:
        response = httpx.get(url, timeout=settings.review_oidc_http_timeout_seconds)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # noqa: BLE001 — 网络/解析失败统一成验签失败
        raise ReviewTokenError("review_oidc_jwks") from exc
    items = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ReviewTokenError("review_oidc_jwks")
    mapped: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        kid = item.get("kid")
        if isinstance(kid, str) and kid:
            mapped[kid] = item
        elif "n" in item and "e" in item:
            # 无 kid 时用材料指纹；仅当令牌也无 kid 时匹配
            mapped[""] = item
    if not mapped:
        raise ReviewTokenError("review_oidc_jwks")
    with _JWKS_LOCK:
        _JWKS_CACHE["url"] = url
        _JWKS_CACHE["fetched_at"] = time.time()
        _JWKS_CACHE["keys"] = mapped
    return dict(mapped)


def clear_oidc_jwks_cache() -> None:
    """测试用：清掉 JWKS 缓存。"""
    with _JWKS_LOCK:
        _JWKS_CACHE["url"] = None
        _JWKS_CACHE["fetched_at"] = 0.0
        _JWKS_CACHE["keys"] = {}


def _rsa_public_from_jwk(jwk: dict[str, Any]):
    if jwk.get("kty") not in (None, "RSA"):
        raise ReviewTokenError("review_token_sig")
    try:
        n = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
        e = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
    except (KeyError, ValueError) as exc:
        raise ReviewTokenError("review_oidc_jwks") from exc
    return RSAPublicNumbers(e, n).public_key()


def _verify_rs256(token: str, jwk: dict[str, Any]) -> dict[str, Any]:
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ReviewTokenError("review_token_malformed")
    head, body, sig = parts
    try:
        header = json.loads(_b64url_decode(head).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReviewTokenError("review_token_malformed") from exc
    if not isinstance(header, dict) or header.get("alg") != "RS256":
        raise ReviewTokenError("review_token_sig")
    public = _rsa_public_from_jwk(jwk)
    try:
        public.verify(
            _b64url_decode(sig),
            f"{head}.{body}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001 — cryptography 校验失败
        raise ReviewTokenError("review_token_sig") from exc
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReviewTokenError("review_token_malformed") from exc
    if not isinstance(payload, dict):
        raise ReviewTokenError("review_token_malformed")
    return payload


def _aud_ok(claim: Any, expected: str) -> bool:
    if isinstance(claim, str):
        return claim == expected
    if isinstance(claim, list):
        return expected in claim
    return False


def _verify_oidc_token(token: str, *, now: int | None = None) -> ReviewPrincipal:
    from ticket_triage.tenants import DEFAULT_TENANT_ID

    issuer = (settings.review_oidc_issuer or "").rstrip("/")
    audience = (settings.review_oidc_audience or "").strip() or "ticket-triage-review"
    if not issuer:
        raise ReviewTokenError("review_oidc_not_configured")

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ReviewTokenError("review_token_malformed")
    try:
        header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReviewTokenError("review_token_malformed") from exc
    if not isinstance(header, dict):
        raise ReviewTokenError("review_token_malformed")
    kid = header.get("kid")
    kid_key = str(kid) if isinstance(kid, str) else ""

    keys = _load_jwks()
    jwk = keys.get(kid_key)
    if jwk is None and kid_key:
        # 可能刚轮换：强制刷新一次
        keys = _load_jwks(force=True)
        jwk = keys.get(kid_key)
    if jwk is None:
        jwk = keys.get("")
    if jwk is None:
        raise ReviewTokenError("review_token_sig")

    payload = _verify_rs256(token, jwk)
    moment = int(now if now is not None else time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int) or moment >= exp:
        raise ReviewTokenError("review_token_expired")
    token_iss = str(payload.get("iss") or "").rstrip("/")
    if token_iss != issuer:
        raise ReviewTokenError("review_token_iss")
    if not _aud_ok(payload.get("aud"), audience):
        raise ReviewTokenError("review_token_aud")
    try:
        sub = clean_reviewer(str(payload.get("sub") or ""))
    except ValueError as exc:
        raise ReviewTokenError("review_token_sub") from exc

    claim = (settings.review_oidc_tenant_claim or "tid").strip() or "tid"
    tid_raw = payload.get(claim) or payload.get("tid") or payload.get("tenant_id") or DEFAULT_TENANT_ID
    tid = str(tid_raw).strip() or DEFAULT_TENANT_ID
    return ReviewPrincipal(
        subject=sub,
        token_id=str(payload["jti"]) if payload.get("jti") else None,
        expires_at=int(exp),
        tenant_id=tid,
    )


def mint_oidc_test_token(
    *,
    private_key,
    subject: str,
    kid: str = "test",
    ttl_seconds: int = 600,
    now: int | None = None,
    tenant_id: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """测试辅助：用 RSA 私钥签发符合当前 OIDC 口径的令牌。"""
    from ticket_triage.tenants import DEFAULT_TENANT_ID

    sub = clean_reviewer(subject)
    issued = int(now if now is not None else time.time())
    iss = (issuer or settings.review_oidc_issuer or "").rstrip("/")
    aud = (audience or settings.review_oidc_audience or "ticket-triage-review").strip()
    tid = (tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    payload = {
        "sub": sub,
        "tid": tid,
        "iss": iss,
        "aud": aud,
        "iat": issued,
        "exp": issued + max(60, ttl_seconds),
        "jti": _b64url_encode(hashlib.sha256(f"oidc:{sub}:{issued}".encode()).digest()[:12]),
    }
    head = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode())
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    signing = f"{head}.{body}".encode()
    signature = private_key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64url_encode(signature)}"


def jwk_from_private_key(private_key, *, kid: str = "test") -> dict[str, Any]:
    """测试辅助：私钥 → 公钥 JWK。"""
    numbers = private_key.public_key().public_numbers()

    def _int_b64(value: int) -> str:
        length = (value.bit_length() + 7) // 8
        return _b64url_encode(value.to_bytes(length, "big"))

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_b64(numbers.n),
        "e": _int_b64(numbers.e),
    }


def _encode(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    head = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode())
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    signing = f"{head}.{body}".encode()
    sig = _b64url_encode(hmac.new(secret.encode(), signing, hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def _decode_hs256(token: str, secret: str, *, now: int | None = None) -> dict[str, Any]:
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ReviewTokenError("review_token_malformed")
    head, body, sig = parts
    signing = f"{head}.{body}".encode()
    expected = _b64url_encode(hmac.new(secret.encode(), signing, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ReviewTokenError("review_token_sig")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReviewTokenError("review_token_malformed") from exc
    if not isinstance(payload, dict):
        raise ReviewTokenError("review_token_malformed")
    moment = int(now if now is not None else time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int) or moment >= exp:
        raise ReviewTokenError("review_token_expired")
    return payload
