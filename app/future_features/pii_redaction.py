"""Configurable PII minimization before a resume/application is sent to an LLM.

This is a conservative regex-based baseline, not anonymization. It masks common structured fields
and labelled names/addresses/identifiers. It can miss multilingual, misspelled, or unlabelled data and
can redact legitimate evidence. For regulated deployments, replace/augment the strategy with a vetted
NER service and jurisdiction-specific review. Raw text remains in the protected database for human
review; provider calls receive only the returned redacted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    counts: dict[str, int]
    changed: bool

    @property
    def categories(self) -> list[str]:
        return sorted(category for category, count in self.counts.items() if count > 0)


# Order matters: remove values in labelled fields, then unlabelled structured identifiers.
_RULES: tuple[tuple[str, re.Pattern[str], str, bool], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[REDACTED_EMAIL]", False),
    (
        "labelled_name",
        re.compile(r"(?im)(\b(?:candidate|applicant|full\s+name|name)\s*:\s*)([^\n,;]{2,80})"),
        r"\1[REDACTED_NAME]",
        False,
    ),
    (
        "self_identified_name",
        re.compile(r"(?i)(\bmy name is\s+)([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})"),
        r"\1[REDACTED_NAME]",
        False,
    ),
    (
        "phone",
        re.compile(r"(?<!\w)(?:\+?\d{1,3}[ .()/-]?)?(?:\(?\d{2,4}\)?[ .()/-]?)?\d{3,4}[ .()/-]\d{3,4}(?!\w)"),
        "[REDACTED_PHONE]",
        False,
    ),
    (
        "government_id",
        re.compile(r"(?i)\b(?:ssn|national\s+id|passport|government\s+id|employee\s+id|tax\s+id)\s*(?:number|no\.?|#)?\s*[:#-]?\s*[A-Z0-9-]{5,}\b"),
        "[REDACTED_ID]",
        False,
    ),
    (
        "labelled_address",
        re.compile(r"(?im)(\b(?:home\s+)?address\s*:\s*)([^\n]{6,180})"),
        r"\1[REDACTED_ADDRESS]",
        False,
    ),
    (
        "street_address",
        re.compile(r"\b\d{1,6}\s+(?:[A-Z0-9.'-]+\s+){1,5}(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Way)\b(?:,?\s*[^\n,]{0,60})?", re.I),
        "[REDACTED_ADDRESS]",
        True,
    ),
)


def redact_pii(text: str, *, enabled: bool = True, policy: str = "baseline") -> RedactionResult:
    """Mask structured PII and expose aggregate counts only, never the matched values."""
    if not enabled:
        return RedactionResult(text=text, counts={}, changed=False)
    if policy not in {"baseline", "strict"}:
        raise ValueError("policy must be 'baseline' or 'strict'")

    output = text
    counts: dict[str, int] = {}
    for category, pattern, replacement, strict_only in _RULES:
        if strict_only and policy != "strict":
            continue
        output, count = pattern.subn(replacement, output)
        if count:
            counts[category] = count
    return RedactionResult(text=output, counts=counts, changed=output != text)
