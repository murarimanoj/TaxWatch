"""Query embeddings must match the model and dimensions used by ingestion."""

from openai import OpenAI, OpenAIError


class QueryEmbeddingError(RuntimeError):
    pass


class QueryEmbedder:
    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        self.model = model
        self.dimensions = dimensions
        self.client = OpenAI(api_key=api_key, timeout=20, max_retries=0)

    def embed_query(self, text: str) -> list[float]:
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            vector = response.data[0].embedding
            if len(vector) != self.dimensions:
                raise QueryEmbeddingError("Embedding dimensions mismatch")
            return vector
        except (OpenAIError, IndexError, ValueError, TypeError) as exc:
            raise QueryEmbeddingError("Query embedding unavailable") from exc
