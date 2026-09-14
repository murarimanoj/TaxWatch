import re


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split normalized text into bounded, overlapping chunks at word boundaries."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and overlap smaller than size")
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end == len(normalized):
            break
        start = max(start + 1, end - overlap)
        while start < end and normalized[start] == " ":
            start += 1
    return chunks
