"""受理 / 审核密钥解析。

生产侧由云厂商 Secret Manager（或 K8s/Docker secrets）把明文挂到文件，
再用 *_FILE 指过去；环境变量只留给本地开发。轮换窗口内可同时接受
当前值与 PREVIOUS，避免切换瞬间全部 401。
"""

from __future__ import annotations

import time
from pathlib import Path

from ticket_triage.config import settings

_KNOWN_CACHE: tuple[float, list[str]] | None = None
_KNOWN_CACHE_TTL_SECONDS = 5.0


def read_secret_value(env_value: str | None, file_path: str | None) -> str | None:
    """文件优先于环境变量。文件缺失或空内容时回退到 env。"""
    if file_path:
        path = Path(file_path)
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    if isinstance(env_value, str):
        text = env_value.strip()
        if text:
            return text
    return None


def configured_keys(
    *,
    env_value: str | None,
    file_path: str | None,
    previous_env: str | None,
    previous_file: str | None,
) -> list[str]:
    """返回鉴权可接受的密钥列表：当前，以及轮换窗口里的上一把。"""
    keys: list[str] = []
    current = read_secret_value(env_value, file_path)
    if current:
        keys.append(current)
    previous = read_secret_value(previous_env, previous_file)
    if previous and previous not in keys:
        keys.append(previous)
    return keys


def service_api_keys() -> list[str]:
    return configured_keys(
        env_value=settings.service_api_key,
        file_path=settings.service_api_key_file,
        previous_env=settings.service_api_key_previous,
        previous_file=settings.service_api_key_previous_file,
    )


def review_api_keys() -> list[str]:
    return configured_keys(
        env_value=settings.review_api_key,
        file_path=settings.review_api_key_file,
        previous_env=settings.review_api_key_previous,
        previous_file=settings.review_api_key_previous_file,
    )


def known_secret_values(*, force_refresh: bool = False) -> list[str]:
    """当前进程已知的密钥明文，供日志脱敏。短缓存避免每条日志读盘。"""
    global _KNOWN_CACHE
    now = time.monotonic()
    if (
        not force_refresh
        and _KNOWN_CACHE is not None
        and now - _KNOWN_CACHE[0] < _KNOWN_CACHE_TTL_SECONDS
    ):
        return _KNOWN_CACHE[1]
    values: list[str] = []
    for key in (*service_api_keys(), *review_api_keys()):
        if key and key not in values:
            values.append(key)
    for raw in (
        settings.openai_api_key,
        settings.anthropic_api_key,
        settings.langfuse_secret_key,
        settings.webhook_signing_secret,
    ):
        if isinstance(raw, str):
            text = raw.strip()
            if text and text not in values:
                values.append(text)
    try:
        from ticket_triage.tenants import known_tenant_secrets

        for text in known_tenant_secrets():
            if text and text not in values:
                values.append(text)
    except Exception:
        pass
    _KNOWN_CACHE = (now, values)
    return values


def clear_secret_cache() -> None:
    """测试或轮换后强制重新读。"""
    global _KNOWN_CACHE
    _KNOWN_CACHE = None
