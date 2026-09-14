import httpx
import pytest

from regulatory_ingestion.embeddings import EmbeddingError, OpenAIEmbedder


def test_embedder_preserves_input_order_and_dimensions():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    embedder = OpenAIEmbedder("test-key", "text-embedding-3-small", 2)
    embedder._client.close()
    embedder._client = httpx.Client(
        base_url="https://api.openai.com/v1", transport=httpx.MockTransport(respond)
    )
    assert embedder.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    embedder.close()


def test_embedder_rejects_wrong_vector_count():
    embedder = OpenAIEmbedder("test-key", "text-embedding-3-small", 2)
    embedder._client.close()
    embedder._client = httpx.Client(
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        ),
    )
    with pytest.raises(EmbeddingError):
        embedder.embed(["one"])
    embedder.close()
