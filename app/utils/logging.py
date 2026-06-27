from __future__ import annotations

import logging
import re


TELEGRAM_BOT_TOKEN_RE = re.compile(r"\bbot(\d+):[A-Za-z0-9_-]+")
OPENAI_API_KEY_RE = re.compile(r"\b(sk-(?:proj-|svcacct-)?)[A-Za-z0-9_-]{8,}")


def mask_telegram_token(value: str) -> str:
    return TELEGRAM_BOT_TOKEN_RE.sub(r"bot\1:***MASKED***", value)


def mask_secrets(value: str) -> str:
    masked = mask_telegram_token(value)
    return OPENAI_API_KEY_RE.sub(r"\1***MASKED***", masked)


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(
                mask_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: mask_secrets(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger().addFilter(SecretMaskingFilter())
