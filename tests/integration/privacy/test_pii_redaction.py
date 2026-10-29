"""Integration tests for app.privacy.pii_redaction module."""

import pytest

from app.privacy.pii_redaction import RedactionResult, redact_pii


def test_redact_pii_disabled(sample_pii_texts: dict[str, str]) -> None:
    """Verify disabled redaction returns original text and zero counts."""
    raw_text = sample_pii_texts["email_and_phone"]
    result = redact_pii(raw_text, enabled=False)

    assert isinstance(result, RedactionResult)
    assert result.text == raw_text
    assert result.counts == {}
    assert result.changed is False
    assert result.categories == []


def test_redact_pii_invalid_policy() -> None:
    """Verify passing an unsupported policy raises ValueError."""
    with pytest.raises(ValueError, match="policy must be 'baseline' or 'strict'"):
        redact_pii("Sample text", enabled=True, policy="unsupported_policy")


def test_redact_pii_baseline_structured_fields(
    sample_pii_texts: dict[str, str]
) -> None:
    """Verify baseline policy masks email, phone, government ID, names, and address."""
    text = (
        f"{sample_pii_texts['email_and_phone']}\n"
        f"{sample_pii_texts['gov_id']}\n"
        f"{sample_pii_texts['labelled_name']}\n"
        f"{sample_pii_texts['self_identified_name']}\n"
        f"{sample_pii_texts['labelled_address']}"
    )

    result = redact_pii(text, enabled=True, policy="baseline")

    assert result.changed is True
    assert "[REDACTED_EMAIL]" in result.text
    assert "[REDACTED_PHONE]" in result.text
    assert "[REDACTED_ID]" in result.text
    assert "[REDACTED_NAME]" in result.text
    assert "[REDACTED_ADDRESS]" in result.text

    # Verify counts structure and categories
    assert result.counts.get("email") == 1
    assert result.counts.get("phone") == 1
    assert result.counts.get("government_id") == 2
    assert result.counts.get("labelled_name") == 2
    assert result.counts.get("self_identified_name") == 1
    assert result.counts.get("labelled_address") == 1

    expected_categories = sorted(
        [
            "email",
            "government_id",
            "labelled_address",
            "labelled_name",
            "phone",
            "self_identified_name",
        ]
    )
    assert result.categories == expected_categories


def test_redact_pii_strict_policy_unlabelled_address(
    sample_pii_texts: dict[str, str]
) -> None:
    """Verify strict policy catches unlabelled street address patterns."""
    raw_address = sample_pii_texts["unlabelled_street_address"]

    # Baseline should not redact unlabelled street address
    baseline_result = redact_pii(raw_address, enabled=True, policy="baseline")
    assert "street_address" not in baseline_result.counts
    assert "123 Main Street" in baseline_result.text

    # Strict policy should redact unlabelled street address
    strict_result = redact_pii(raw_address, enabled=True, policy="strict")
    assert strict_result.changed is True
    assert strict_result.counts.get("street_address") == 1
    assert "street_address" in strict_result.categories
    assert "[REDACTED_ADDRESS]" in strict_result.text
    assert "123 Main Street" not in strict_result.text


def test_redact_pii_clean_text_no_redactions(
    sample_pii_texts: dict[str, str]
) -> None:
    """Verify clean text returns changed=False and empty counts."""
    clean = sample_pii_texts["clean_text"]
    result = redact_pii(clean, enabled=True, policy="strict")

    assert result.text == clean
    assert result.changed is False
    assert result.counts == {}
    assert result.categories == []


def test_redaction_result_metadata_privacy_safety() -> None:
    """Verify RedactionResult metadata exposes no raw PII strings."""
    sensitive_text = "Name: Confidential Candidate, Email: secret@corporation.org"
    result = redact_pii(sensitive_text, enabled=True, policy="baseline")

    # Counts dictionary only stores integer frequencies
    for key, value in result.counts.items():
        assert isinstance(key, str)
        assert isinstance(value, int)
        assert "Confidential" not in key
        assert "secret" not in key

    # categories property returns sorted list of categories with count > 0
    assert result.categories == ["email", "labelled_name"]
    assert "Confidential" not in str(result.categories)
    assert "secret" not in str(result.categories)
