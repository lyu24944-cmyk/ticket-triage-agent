"""轻量多租户：单库 + tenant_id；密钥 / Webhook / 回调 / 队列配额按租户。

未配置 TENANTS_JSON 时回退为单租户 default，沿用 SERVICE_API_KEY / REVIEW_API_KEY。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass

from ticket_triage.config import settings

DEFAULT_TENANT_ID = "default"

_CACHE: tuple[float, dict[str, "TenantConfig"]] | None = None
_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str
    service_api_key: str | None = None
    review_api_key: str | None = None
    webhook_secret: str | None = None
    callback_url: str | None = None
    queue_limit: int | None = None


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    queue_limit: int
    callback_url: str | None = None
    webhook_secret: str | None = None


def _same_secret(provided: str, expected: str) -> bool:
    return secrets.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def clear_tenant_cache() -> None:
    global _CACHE
    _CACHE = None


def _default_tenant() -> TenantConfig:
    from ticket_triage.secrets import review_api_keys, service_api_keys

    service_keys = service_api_keys()
    review_keys = review_api_keys()
    return TenantConfig(
        tenant_id=DEFAULT_TENANT_ID,
        service_api_key=service_keys[0] if service_keys else None,
        review_api_key=review_keys[0] if review_keys else None,
        webhook_secret=settings.webhook_signing_secret,
        callback_url=settings.closure_callback_url,
        queue_limit=max(1, int(settings.run_queue_limit)),
    )


def _parse_tenants_json(raw: str) -> dict[str, TenantConfig]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("TENANTS_JSON must be a JSON array")
    out: dict[str, TenantConfig] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("tenant entry must be an object")
        tid = str(item.get("tenant_id") or "").strip()
        if not tid:
            raise ValueError("tenant_id is required")
        ql = item.get("queue_limit")
        out[tid] = TenantConfig(
            tenant_id=tid,
            service_api_key=(str(item["service_api_key"]).strip() or None)
            if item.get("service_api_key")
            else None,
            review_api_key=(str(item["review_api_key"]).strip() or None)
            if item.get("review_api_key")
            else None,
            webhook_secret=(str(item["webhook_secret"]).strip() or None)
            if item.get("webhook_secret")
            else None,
            callback_url=(str(item["callback_url"]).strip() or None)
            if item.get("callback_url")
            else None,
            queue_limit=int(ql) if ql is not None else None,
        )
    return out


def load_tenants(*, force_refresh: bool = False) -> dict[str, TenantConfig]:
    """tenant_id → 配置。短缓存，测试可 clear_tenant_cache。"""
    global _CACHE
    now = time.monotonic()
    if (
        not force_refresh
        and _CACHE is not None
        and now - _CACHE[0] < _CACHE_TTL_SECONDS
    ):
        return _CACHE[1]
    raw = settings.tenants_json
    if isinstance(raw, str) and raw.strip():
        tenants = _parse_tenants_json(raw.strip())
    else:
        tenants = {DEFAULT_TENANT_ID: _default_tenant()}
    _CACHE = (now, tenants)
    return tenants


def get_tenant(tenant_id: str) -> TenantConfig | None:
    return load_tenants().get(tenant_id)


def context_for(tenant_id: str | None) -> TenantContext:
    tid = (tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    cfg = get_tenant(tid) or TenantConfig(tenant_id=tid)
    limit = cfg.queue_limit if cfg.queue_limit is not None else max(1, int(settings.run_queue_limit))
    return TenantContext(
        tenant_id=cfg.tenant_id,
        queue_limit=max(1, int(limit)),
        callback_url=cfg.callback_url or settings.closure_callback_url,
        webhook_secret=cfg.webhook_secret or settings.webhook_signing_secret,
    )


def resolve_service_key(provided: str | None) -> TenantContext | None:
    """X-API-Key 对上某租户 service 密钥则返回上下文。"""
    candidate = provided or ""
    if not candidate:
        return None
    # 先扫 TENANTS_JSON；再扫单租户轮换窗口里的 service keys。
    for cfg in load_tenants().values():
        if cfg.service_api_key and _same_secret(candidate, cfg.service_api_key):
            return context_for(cfg.tenant_id)
    from ticket_triage.secrets import service_api_keys

    keys = service_api_keys()
    if keys and any(_same_secret(candidate, key) for key in keys):
        return context_for(DEFAULT_TENANT_ID)
    return None


def resolve_review_key(provided: str | None) -> TenantContext | None:
    """换票用的 REVIEW_API_KEY → 租户（写入 JWT tenant_id）。"""
    candidate = provided or ""
    if not candidate:
        return None
    for cfg in load_tenants().values():
        if cfg.review_api_key and _same_secret(candidate, cfg.review_api_key):
            return context_for(cfg.tenant_id)
    from ticket_triage.secrets import review_api_keys

    keys = review_api_keys()
    if keys and any(_same_secret(candidate, key) for key in keys):
        return context_for(DEFAULT_TENANT_ID)
    return None


def resolve_webhook_secret(provided: str | None) -> TenantContext | None:
    """Webhook 签名密钥 → 租户。"""
    candidate = provided or ""
    if not candidate:
        return None
    # 调用方传入的是「已验证签名所用的 secret」本身，不是 HTTP 头里的签名。
    for cfg in load_tenants().values():
        secret = cfg.webhook_secret or (
            settings.webhook_signing_secret if cfg.tenant_id == DEFAULT_TENANT_ID else None
        )
        if secret and _same_secret(candidate, secret):
            return context_for(cfg.tenant_id)
    if settings.webhook_signing_secret and _same_secret(
        candidate, settings.webhook_signing_secret
    ):
        return context_for(DEFAULT_TENANT_ID)
    return None


def find_tenant_by_webhook_body_hmac(
    raw_body: bytes,
    signature_header: str | None,
    *,
    prefix: str = "sha256=",
) -> TenantContext | None:
    """用 raw body + 各租户 webhook_secret 验签，命中则返回租户。"""
    if not signature_header:
        return None
    provided = signature_header.strip()
    if provided.lower().startswith(prefix):
        provided = provided[len(prefix) :]
    provided = provided.strip()
    if not provided:
        return None
    tried: set[str] = set()
    for cfg in load_tenants().values():
        secret = cfg.webhook_secret
        if not secret and cfg.tenant_id == DEFAULT_TENANT_ID:
            secret = settings.webhook_signing_secret
        if not secret or secret in tried:
            continue
        tried.add(secret)
        digest = hashlib.sha256()
        # HMAC-SHA256(secret, body) hex
        import hmac as hmac_mod

        expected = hmac_mod.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if secrets.compare_digest(expected, provided):
            return context_for(cfg.tenant_id)
    if settings.webhook_signing_secret and settings.webhook_signing_secret not in tried:
        import hmac as hmac_mod

        expected = hmac_mod.new(
            settings.webhook_signing_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if secrets.compare_digest(expected, provided):
            return context_for(DEFAULT_TENANT_ID)
    return None


def known_tenant_secrets() -> list[str]:
    """供日志脱敏。"""
    values: list[str] = []
    for cfg in load_tenants().values():
        for raw in (cfg.service_api_key, cfg.review_api_key, cfg.webhook_secret):
            if raw and raw not in values:
                values.append(raw)
    if settings.webhook_signing_secret and settings.webhook_signing_secret not in values:
        values.append(settings.webhook_signing_secret)
    return values
