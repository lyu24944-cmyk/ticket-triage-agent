"""日志里禁止出现密钥明文。"""

from __future__ import annotations

import logging

from ticket_triage.secrets import known_secret_values

_INSTALLED = False
_PLACEHOLDER = "[REDACTED_SECRET]"


class SecretRedactFilter(logging.Filter):
    """把已知密钥从日志消息里换成占位符。"""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = known_secret_values()
        if not secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for secret in secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, _PLACEHOLDER)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_secret_redact_filter() -> None:
    """挂到根 logger，只装一次。"""
    global _INSTALLED
    if _INSTALLED:
        return
    logging.getLogger().addFilter(SecretRedactFilter())
    _INSTALLED = True
