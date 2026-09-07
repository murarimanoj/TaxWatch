import hashlib
from datetime import date, datetime

from .domain import Source


def document_hash(
    source: Source | str,
    published_date: date | datetime | None,
    title: str,
) -> str:
    if isinstance(published_date, datetime):
        published_date = published_date.date()
    date_value = published_date.isoformat() if published_date else ""
    source_value = source.value if isinstance(source, Source) else source
    identity = f"{source_value}\x1f{date_value}\x1f{title}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
