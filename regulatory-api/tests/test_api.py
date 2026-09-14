from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from regulatory_api.app import Settings, create_app, get_repository
from regulatory_api.models import Authority, DocumentPage, Overview
from regulatory_api.repository import DashboardRepository, document_filter


@pytest.fixture
def repository() -> MagicMock:
    return MagicMock(spec=DashboardRepository)


@pytest.fixture
def client(repository: MagicMock) -> TestClient:
    app = create_app(Settings(_env_file=None, mongodb_uri=None))
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app)


def test_document_filter_escapes_regex_and_bounds_year() -> None:
    assert document_filter(Authority.RBI, " [tax].* ", 2026) == {
        "source": "rbi",
        "title": {"$regex": r"\[tax\]\.\*", "$options": "i"},
        "published_date": {
            "$gte": datetime(2026, 1, 1, tzinfo=UTC),
            "$lt": datetime(2027, 1, 1, tzinfo=UTC),
        },
    }
    assert document_filter(None, "  ", None) == {}


def test_documents_passes_validated_parameters(
    client: TestClient, repository: MagicMock
) -> None:
    repository.documents.return_value = DocumentPage(
        items=[], total=0, page=2, page_size=10
    )
    response = client.get(
        "/api/documents?source=gst&q=tax&year=2026&page=2&page_size=10"
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 2, "page_size": 10}
    repository.documents.assert_called_once_with(Authority.GST, "tax", 2026, 2, 10)


@pytest.mark.parametrize(
    "query", ["source=unknown", "page=0", "page_size=101", "year=9999", "year=abc"]
)
def test_invalid_filters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/documents?{query}").status_code == 422


def test_overview(client: TestClient, repository: MagicMock) -> None:
    repository.overview.return_value = Overview(total=0, authorities=[])
    assert client.get("/api/overview").json() == {"total": 0, "authorities": []}


def test_missing_document(client: TestClient, repository: MagicMock) -> None:
    repository.detail.return_value = None
    assert client.get("/api/documents/rbi/missing").status_code == 404
    repository.detail.assert_called_once_with(Authority.RBI, "missing")


def test_database_failure_is_sanitized(
    client: TestClient, repository: MagicMock
) -> None:
    repository.overview.side_effect = ServerSelectionTimeoutError("secret-host")
    response = client.get("/api/overview")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert "secret-host" not in response.text


def test_unconfigured_database_and_health() -> None:
    with TestClient(create_app(Settings(_env_file=None, mongodb_uri=None))) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/overview").status_code == 503
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == (
            "http://localhost:5173"
        )
        assert (
            "access-control-allow-origin"
            not in client.get(
                "/health", headers={"Origin": "https://untrusted.example"}
            ).headers
        )


def test_repository_pagination_and_projection() -> None:
    database = MagicMock()
    database.regulatory_documents.count_documents.return_value = 50
    database.regulatory_documents.aggregate.return_value = []
    result = DashboardRepository(database).documents(
        source=Authority.CBDT, page=3, page_size=10
    )
    assert result.total == 50
    pipeline = database.regulatory_documents.aggregate.call_args.args[0]
    assert pipeline[:4] == [
        {"$match": {"source": "cbdt"}},
        {"$sort": {"published_date": -1, "_id": -1}},
        {"$skip": 20},
        {"$limit": 10},
    ]
    assert "content" not in pipeline[4]["$project"]
    assert pipeline[4]["$project"]["excerpt"]["$substrCP"][2] == 320


def test_overview_counts_and_failed_run() -> None:
    database = MagicMock()
    database.regulatory_documents.count_documents.return_value = 3
    database.regulatory_documents.aggregate.return_value = []
    checked = datetime(2026, 9, 1, tzinfo=UTC)
    database.ingestion_runs.find_one.return_value = {
        "recorded_at": checked,
        "failed": 1,
    }
    result = DashboardRepository(database).overview()
    assert result.total == 15
    assert {item.source.value for item in result.authorities} == {
        "rbi",
        "cbdt",
        "sebi",
        "gst",
        "mca",
    }
    assert all(item.last_run_status == "failed" for item in result.authorities)
    assert all(item.last_checked_at == checked for item in result.authorities)
    assert all(item.latest_year is None for item in result.authorities)


def test_document_detail_serializes_existing_bson(
    client: TestClient, repository: MagicMock
) -> None:
    repository.detail.return_value = {
        "source": "rbi",
        "document_hash": "abc",
        "title": "A publication",
        "document_type": "notification",
        "detail_url": "https://example.org/document",
        "published_date": datetime(2026, 9, 1, tzinfo=UTC),
        "content": "Extracted publication text",
    }
    response = client.get("/api/documents/rbi/abc")
    assert response.status_code == 200
    assert response.json()["published_date"] == "2026-09-01T00:00:00Z"
    assert response.json()["content"] == "Extracted publication text"


@pytest.mark.parametrize("query", ["", "?source=mca"])
def test_mca_document_listing(query: str) -> None:
    database = MagicMock()
    database.regulatory_documents.count_documents.return_value = 1
    database.regulatory_documents.aggregate.return_value = [
        {
            "source": "mca",
            "document_hash": "mca-document",
            "title": "MCA notification",
            "document_type": "notification",
            "detail_url": "https://www.mca.gov.in/notification",
            "published_date": datetime(2026, 9, 1, tzinfo=UTC),
            "excerpt": "MCA publication text",
        }
    ]
    app = create_app(Settings(_env_file=None, mongodb_uri=None))
    app.dependency_overrides[get_repository] = lambda: DashboardRepository(database)
    with TestClient(app) as client:
        response = client.get(f"/api/documents{query}")
    assert response.status_code == 200
    assert response.json()["items"][0]["source"] == "mca"
    filters = {"source": "mca"} if query else {}
    database.regulatory_documents.count_documents.assert_called_once_with(filters)


def test_mca_detail(client: TestClient, repository: MagicMock) -> None:
    repository.detail.return_value = {
        "source": "mca",
        "document_hash": "mca-document",
        "title": "MCA notification",
        "document_type": "notification",
        "detail_url": "https://www.mca.gov.in/notification",
        "content": "MCA publication text",
    }
    response = client.get("/api/documents/mca/mca-document")
    assert response.status_code == 200
    assert response.json()["source"] == "mca"
    repository.detail.assert_called_once_with(Authority.MCA, "mca-document")


@pytest.mark.parametrize("year", [None, 2026])
def test_overview_passes_year(
    client: TestClient, repository: MagicMock, year: int | None
) -> None:
    repository.overview.return_value = Overview(total=0, authorities=[])
    response = client.get("/api/overview", params={"year": year} if year else {})
    assert response.status_code == 200
    repository.overview.assert_called_once_with(year=year)


@pytest.mark.parametrize("year", [1899, 9999, "invalid"])
def test_overview_rejects_invalid_year(client: TestClient, year: int | str) -> None:
    assert client.get("/api/overview", params={"year": year}).status_code == 422


def test_overview_filters_counts_and_previews_by_year() -> None:
    database = MagicMock()
    database.regulatory_documents.count_documents.return_value = 2
    database.regulatory_documents.aggregate.return_value = []
    database.ingestion_runs.find_one.return_value = None
    result = DashboardRepository(database).overview(year=2026)
    assert result.total == 2 * len(Authority)
    count_filters = [
        call.args[0]
        for call in database.regulatory_documents.count_documents.call_args_list
    ]
    preview_filters = [
        call.args[0][0]["$match"]
        for call in database.regulatory_documents.aggregate.call_args_list
    ]
    assert count_filters == preview_filters
    assert all(
        call.args[0][3] == {"$limit": 3}
        for call in database.regulatory_documents.aggregate.call_args_list
    )
    assert {item["source"] for item in count_filters} == {
        source.value for source in Authority
    }
    assert all(
        item["published_date"]
        == {
            "$gte": datetime(2026, 1, 1, tzinfo=UTC),
            "$lt": datetime(2027, 1, 1, tzinfo=UTC),
        }
        for item in count_filters
    )
