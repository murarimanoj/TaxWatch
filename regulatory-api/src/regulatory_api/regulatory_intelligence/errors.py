"""Bounded diagnostic fields for operator-only processing records."""

import re
import traceback
from collections.abc import Iterable


def error_details(
    exc: Exception, sensitive_values: Iterable[str] = ()
) -> dict[str, str]:
    def sanitize(text: str, limit: int) -> str:
        for secret in sorted(set(sensitive_values), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"(?i)(mongodb(?:\+srv)?|https?)://[^\s/@]+:[^\s/@]+@",
            r"\1://[REDACTED]@",
            text,
        )
        text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
        text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [REDACTED]", text)
        text = re.sub(
            r"(?i)((?:api[_-]?key|password|token|secret)[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s&,;]+)",
            r"\1[REDACTED]",
            text,
        )
        if len(text) > limit:
            return text[:limit] + "\n[truncated]"
        return text

    # Chained exceptions are included; stack-frame locals are deliberately omitted.
    trace = "".join(
        traceback.TracebackException.from_exception(exc, capture_locals=False).format(
            chain=True
        )
    )
    return {
        "error_type": type(exc).__name__,
        "error_message": sanitize(str(exc), 8000),
        "error_trace": sanitize(trace, 32000),
    }
