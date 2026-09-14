import httpx


class EmbeddingError(RuntimeError):
    """The embedding provider failed or returned an unusable vector."""


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        self.model = model
        self.dimensions = dimensions
        self._client = httpx.Client(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors: list[list[float]] = []
            for offset in range(0, len(texts), 64):
                response = self._client.post(
                    "/embeddings",
                    json={
                        "model": self.model,
                        "input": texts[offset : offset + 64],
                        "dimensions": self.dimensions,
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                vectors.extend(item["embedding"] for item in data)
            if len(vectors) != len(texts) or any(
                len(vector) != self.dimensions for vector in vectors
            ):
                raise EmbeddingError("Embedding response count or dimensions mismatch")
            return vectors
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("Could not create embeddings") from exc

    def close(self) -> None:
        self._client.close()
