import json
from unittest.mock import MagicMock, patch

import pytest

from regulatory_api.regulatory_intelligence.cli import main
from regulatory_api.regulatory_intelligence.service import IntelligenceService


def test_analysis_batch_continues_and_summarizes():
    service = IntelligenceService(MagicMock())
    service._records = MagicMock(
        return_value=iter(
            [
                {"_id": 1, "document_hash": "a"},
                {"_id": 2, "document_hash": "b"},
                {"_id": 3, "document_hash": "c"},
                {"_id": 4},
            ]
        )
    )
    service.analyze = MagicMock(
        side_effect=[
            {"analysis_id": "run-a", "status": "pending_review"},
            ValueError("secret detail"),
            {"analysis_id": "run-c", "status": "already_claimed_or_completed"},
        ]
    )
    result = service.analyze_source("cbdt")
    assert (
        result["total"],
        result["completed"],
        result["failed"],
        result["skipped"],
    ) == (4, 1, 2, 1)
    assert result["completed_analyses"][0]["analysis_id"] == "run-a"
    assert result["failures"][0]["document_hash"] == "b"
    assert "secret" not in json.dumps(result)
    assert service.analyze.call_args_list[-1].args == ("cbdt", "c")


def test_empty_source():
    service = IntelligenceService(MagicMock())
    service._records = MagicMock(return_value=iter([]))
    assert service.analyze_source("rbi")["total"] == 0


def test_keyset_pages_close_cursor_before_processing():
    service = IntelligenceService(MagicMock())
    collection = MagicMock()
    first, empty = MagicMock(), MagicMock()
    first.__enter__.return_value = iter([{"_id": 1}, {"_id": 2}])
    empty.__enter__.return_value = iter([])
    collection.find.return_value.sort.return_value.limit.side_effect = [first, empty]
    records = service._records(collection, {"source": "rbi"}, {"document_hash": 1})
    assert next(records) == {"_id": 1}
    first.__exit__.assert_called_once()
    assert list(records) == [{"_id": 2}]
    assert collection.find.call_args.args[0] == {"source": "rbi", "_id": {"$gt": 2}}


@pytest.mark.parametrize(
    "decision,verification", [("approved", "verified"), ("rejected", "rejected")]
)
def test_review_updates_impact_and_relationships_transactionally(
    decision, verification
):
    db = MagicMock()
    service = IntelligenceService(db)
    service.usable = MagicMock(return_value=True)
    db.regulatory_impacts.update_one.return_value.matched_count = 1
    session = db.client.start_session.return_value.__enter__.return_value
    session.with_transaction.side_effect = lambda callback: callback(session)
    service.review("run-a", "reviewer", decision)
    impact = db.regulatory_impacts.update_one.call_args
    edges = db.document_relationships.update_many.call_args
    assert impact.args[1]["$set"]["review_status"] == decision
    assert edges.args[0] == {"analysis_id": "run-a"}
    assert edges.args[1]["$set"] == {
        "review_status": decision,
        "verification_status": verification,
    }
    assert impact.args[1]["$push"]["reviews"] == edges.args[1]["$push"]["reviews"]
    assert impact.kwargs["session"] is session
    assert edges.kwargs["session"] is session


def test_stale_review_does_not_write():
    db = MagicMock()
    service = IntelligenceService(db)
    service.usable = MagicMock(return_value=False)
    with pytest.raises(ValueError, match="stale"):
        service.review("old", "reviewer", "approved")
    db.client.start_session.assert_not_called()


def test_bulk_review_repairs_same_decision_and_continues_failures():
    service = IntelligenceService(MagicMock())
    service._records = MagicMock(
        return_value=iter([{"_id": "a"}, {"_id": "b"}, {"_id": "c"}])
    )
    service.review = MagicMock(side_effect=[None, ValueError("stale"), None])
    result = service.review_source("cbdt", "reviewer", "approved")
    assert result["completed"] == 2
    assert result["failed"] == 1
    assert result["completed_analyses"] == ["a", "c"]
    query = service._records.call_args.args[1]
    assert query["document_id"] == {"$regex": "^cbdt:"}
    assert query["review_status"]["$in"] == ["pending_review", "approved"]


@pytest.mark.parametrize(
    "arguments,method",
    [
        (["analyze-document", "--source", "cbdt"], "analyze_source"),
        (
            ["analyze-document", "--source", "cbdt", "--document-hash", "hash"],
            "analyze",
        ),
        (
            [
                "review",
                "--source",
                "cbdt",
                "--reviewer",
                "ca",
                "--decision",
                "approved",
            ],
            "review_source",
        ),
        (
            [
                "review",
                "--analysis-id",
                "run",
                "--reviewer",
                "ca",
                "--decision",
                "approved",
            ],
            "review",
        ),
    ],
)
def test_cli_dispatch(arguments, method, capsys):
    with patch("sys.argv", ["cli", *arguments]), patch(
        "regulatory_api.regulatory_intelligence.cli.Settings"
    ), patch("regulatory_api.regulatory_intelligence.cli.MongoClient"), patch(
        "regulatory_api.regulatory_intelligence.cli.LangChainClient"
    ), patch(
        "regulatory_api.regulatory_intelligence.cli.IntelligenceService"
    ) as factory:
        getattr(factory.return_value, method).return_value = {
            "completed": 1,
            "failed": 0,
        }
        main()
        getattr(factory.return_value, method).assert_called_once()
        json.loads(capsys.readouterr().out)


def test_cli_batch_failure_prints_summary_and_exits_nonzero(capsys):
    with patch("sys.argv", ["cli", "analyze-document", "--source", "rbi"]), patch(
        "regulatory_api.regulatory_intelligence.cli.Settings"
    ), patch("regulatory_api.regulatory_intelligence.cli.MongoClient"), patch(
        "regulatory_api.regulatory_intelligence.cli.LangChainClient"
    ), patch(
        "regulatory_api.regulatory_intelligence.cli.IntelligenceService"
    ) as factory:
        factory.return_value.analyze_source.return_value = {"completed": 2, "failed": 1}
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert json.loads(capsys.readouterr().out) == {"completed": 2, "failed": 1}
