"""FUTURE FEATURE: append-only audit events.

Implementation brief:
- Add an immutable audit_events table with event ID, UTC timestamp, actor/service identity,
  request ID, action, resource ID, result, and a redacted metadata JSON field.
- Record record access, deletion, configuration changes, model/provider version, and reviewer actions.
- Never copy full application text, API keys, or unredacted model prompts into events.
- Restrict write access to an audit service role; define retention and tamper detection.
- Add tests proving events are emitted for create/read/delete and that sensitive fields are redacted.
"""
