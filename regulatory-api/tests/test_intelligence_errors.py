from regulatory_api.regulatory_intelligence.errors import error_details


def test_chained_trace_redacts_secrets_and_omits_locals():
    private_local = "never-capture-local-value"
    try:
        try:
            raise ValueError("mongodb+srv://user:pass@host/db api_key=sk-test123")
        except ValueError as exc:
            raise RuntimeError("Bearer abc123 configured-credential") from exc
    except RuntimeError as exc:
        result = error_details(exc, ["configured-credential"])
    assert private_local not in result["error_trace"]
    assert "ValueError" in result["error_trace"]
    assert "RuntimeError" in result["error_trace"]
    assert "direct cause" in result["error_trace"]
    for secret in ("user:pass", "sk-test123", "abc123", "configured-credential"):
        assert secret not in str(result)
    assert "[REDACTED]" in result["error_message"]


def test_error_fields_are_bounded():
    result = error_details(ValueError("x" * 100000))
    assert len(result["error_message"]) < 8100
    assert len(result["error_trace"]) < 32100
    assert result["error_message"].endswith("[truncated]")


def test_plain_message_is_preserved():
    result = error_details(ValueError("Evidence quote not found in supplied version"))
    assert result["error_message"] == "Evidence quote not found in supplied version"
    assert result["error_type"] == "ValueError"
