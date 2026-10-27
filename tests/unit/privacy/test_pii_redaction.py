"""Unit tests for app.privacy.pii_redaction module.

Tests cover:
- Disabled redaction returning original text unaltered.
- Invalid policy validation (raising ValueError).
- Baseline policy redaction (emails, labelled names, self-identified names, phone numbers, government IDs, labelled addresses).
- Baseline policy street address exemption (unlabelled street addresses kept intact).
- Strict policy redaction (redacting unlabelled street addresses).
- RedactionResult properties (counts, changed boolean flag, and sorted categories).
"""

from __future__ import annotations

import unittest

from app.privacy.pii_redaction import RedactionResult, redact_pii


class TestPIIRedactionDisabled(unittest.TestCase):
    """Test behavior when PII redaction is disabled."""

    def test_disabled_redaction_returns_original_text_unaltered(self) -> None:
        raw_text = (
            "Candidate Name: Jane Doe, Email: jane.doe@example.com, Phone: +1-555-0199"
        )
        result = redact_pii(raw_text, enabled=False)

        self.assertIsInstance(result, RedactionResult)
        self.assertEqual(result.text, raw_text)
        self.assertEqual(result.counts, {})
        self.assertFalse(result.changed)
        self.assertEqual(result.categories, [])


class TestPIIRedactionPolicyValidation(unittest.TestCase):
    """Test policy string validation."""

    def test_invalid_policy_raises_value_error(self) -> None:
        invalid_policies = ["invalid", "CUSTOM", "none", "strict_v2", ""]
        for policy in invalid_policies:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError) as ctx:
                    redact_pii("Sample text", enabled=True, policy=policy)
                self.assertIn(
                    "policy must be 'baseline' or 'strict'", str(ctx.exception)
                )


class TestPIIRedactionBaselinePolicy(unittest.TestCase):
    """Test baseline policy redaction rules and exceptions."""

    def test_email_redaction(self) -> None:
        text = "Contact me at alice.smith@enterprise.org or bob_dev123@gmail.co.uk for details."
        result = redact_pii(text, enabled=True, policy="baseline")

        self.assertNotIn("alice.smith@enterprise.org", result.text)
        self.assertNotIn("bob_dev123@gmail.co.uk", result.text)
        self.assertIn("[REDACTED_EMAIL]", result.text)
        self.assertEqual(result.counts.get("email"), 2)
        self.assertTrue(result.changed)
        self.assertIn("email", result.categories)

    def test_labelled_name_redaction(self) -> None:
        text = "Candidate: Johnathan Smith\nApplicant: Sarah Jenkins\nFull Name: Robert Paulson"
        result = redact_pii(text, enabled=True, policy="baseline")

        self.assertNotIn("Johnathan Smith", result.text)
        self.assertNotIn("Sarah Jenkins", result.text)
        self.assertNotIn("Robert Paulson", result.text)
        self.assertIn("Candidate: [REDACTED_NAME]", result.text)
        self.assertIn("Applicant: [REDACTED_NAME]", result.text)
        self.assertIn("Full Name: [REDACTED_NAME]", result.text)
        self.assertEqual(result.counts.get("labelled_name"), 3)

    def test_self_identified_name_redaction(self) -> None:
        text = "Hello, my name is Michael Scott and I am applying for the role."
        result = redact_pii(text, enabled=True, policy="baseline")

        self.assertNotIn("Michael Scott", result.text)
        self.assertIn("my name is [REDACTED_NAME]", result.text)
        self.assertEqual(result.counts.get("self_identified_name"), 1)

    def test_phone_number_redaction(self) -> None:
        text = "Call +1 (555) 234-5678 or 555.876.5432 or +44 20 7946 0912."
        result = redact_pii(text, enabled=True, policy="baseline")

        self.assertNotIn("(555) 234-5678", result.text)
        self.assertNotIn("555.876.5432", result.text)
        self.assertIn("[REDACTED_PHONE]", result.text)
        self.assertGreaterEqual(result.counts.get("phone", 0), 2)

    def test_government_id_redaction(self) -> None:
        text = "SSN: 123-45-6789, National ID # ABC98765, Passport No: X1234567, Employee ID: EMP-99881."
        result = redact_pii(text, enabled=True, policy="baseline")

        self.assertNotIn("123-45-6789", result.text)
        self.assertNotIn("ABC98765", result.text)
        self.assertNotIn("X1234567", result.text)
        self.assertNotIn("EMP-99881", result.text)
        self.assertIn("[REDACTED_ID]", result.text)
        self.assertEqual(result.counts.get("government_id"), 4)

    def test_labelled_address_redaction(self) -> None:
        text = "Address: 742 Evergreen Terrace, Springfield, OR 97477\nHome Address: 221B Baker Street, London"
        result = redact_pii(text, enabled=True, policy="baseline")

        self.assertNotIn("742 Evergreen Terrace", result.text)
        self.assertNotIn("221B Baker Street", result.text)
        self.assertIn("Address: [REDACTED_ADDRESS]", result.text)
        self.assertIn("Home Address: [REDACTED_ADDRESS]", result.text)
        self.assertEqual(result.counts.get("labelled_address"), 2)

    def test_unlabelled_street_address_not_redacted_in_baseline(self) -> None:
        text = (
            "We met near 100 Pennsylvania Avenue, Washington DC to discuss the project."
        )
        result = redact_pii(text, enabled=True, policy="baseline")

        # In baseline policy, unlabelled street addresses must NOT be redacted
        self.assertIn("100 Pennsylvania Avenue", result.text)
        self.assertNotIn("street_address", result.counts)


class TestPIIRedactionStrictPolicy(unittest.TestCase):
    """Test strict policy redaction rules."""

    def test_unlabelled_street_address_redacted_in_strict(self) -> None:
        text = "The candidate formerly worked at 500 Oracle Drive, Redwood Shores, CA."
        result = redact_pii(text, enabled=True, policy="strict")

        self.assertNotIn("500 Oracle Drive", result.text)
        self.assertIn("[REDACTED_ADDRESS]", result.text)
        self.assertEqual(result.counts.get("street_address"), 1)
        self.assertTrue(result.changed)
        self.assertIn("street_address", result.categories)

    def test_strict_policy_applies_both_baseline_and_strict_rules(self) -> None:
        text = (
            "Name: Alice Walker\n"
            "Email: alice@company.com\n"
            "Office: 123 Science Drive, Suite 400"
        )
        result = redact_pii(text, enabled=True, policy="strict")

        self.assertNotIn("Alice Walker", result.text)
        self.assertNotIn("alice@company.com", result.text)
        self.assertNotIn("123 Science Drive", result.text)
        self.assertIn("labelled_name", result.categories)
        self.assertIn("email", result.categories)
        self.assertIn("street_address", result.categories)


class TestRedactionResultProperties(unittest.TestCase):
    """Test RedactionResult dataclass properties and immutability."""

    def test_counts_accuracy_and_changed_flag(self) -> None:
        text_no_pii = "Experienced Python developer with 5 years of background in distributed systems."
        res_no_pii = redact_pii(text_no_pii, enabled=True, policy="baseline")

        self.assertEqual(res_no_pii.counts, {})
        self.assertFalse(res_no_pii.changed)
        self.assertEqual(res_no_pii.categories, [])

        text_multi_pii = (
            "Email: dev@test.com, Email: admin@test.com, Phone: 555-123-4567"
        )
        res_multi_pii = redact_pii(text_multi_pii, enabled=True, policy="baseline")

        self.assertTrue(res_multi_pii.changed)
        self.assertEqual(res_multi_pii.counts.get("email"), 2)
        self.assertEqual(res_multi_pii.counts.get("phone"), 1)

    def test_categories_property_returns_sorted_unique_active_categories(self) -> None:
        text = "SSN: 999-88-7766, Email: test@sample.com, Name: John Doe"
        res = redact_pii(text, enabled=True, policy="baseline")

        categories = res.categories
        self.assertEqual(categories, sorted(categories))
        self.assertEqual(set(categories), {"email", "government_id", "labelled_name"})


if __name__ == "__main__":
    unittest.main()
