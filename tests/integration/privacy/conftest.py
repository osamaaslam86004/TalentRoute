"""Fixtures for PII redaction tests."""

import pytest


@pytest.fixture
def sample_pii_texts() -> dict[str, str]:
    return {
        "email_and_phone": "Please contact me at jane.doe@example.com or +1 (555) 019-2834.",
        "gov_id": "My SSN: 123-45-6789 and Passport Number: AB1234567.",
        "labelled_name": "Applicant: Jane Smith\nFull Name: Johnathan Doe",
        "self_identified_name": "Hello, my name is Alex Rivera and I am applying for the role.",
        "labelled_address": "Home Address: 742 Evergreen Terrace, Springfield, OR 97477",
        "unlabelled_street_address": "I live at 123 Main Street, Suite 400, Austin, TX 78701.",
        "clean_text": "Experienced Python developer with 5 years in FastAPI and SQLAlchemy.",
    }
