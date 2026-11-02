import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    occurred_at: datetime
    tenant_id: str
    sequence_no: int
    previous_hash: str
    event_hash: str
    actor_subject: str
    actor_roles: list[str]
    request_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    event_metadata: dict[str, object] = Field(serialization_alias="metadata")


class AuditEventList(BaseModel):
    items: list[AuditEventRead]
    limit: int
    offset: int
    count: int


class AuditIntegrityRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    tenant_id: str
    valid: bool
    event_count: int
    head_sequence: int
    first_failure_sequence: int | None = Field(default=None, alias="first_failure")
    first_failure: int | None = Field(default=None, alias="first_failure_sequence")
    failure_reason: str | None = None
    head_hash: str
