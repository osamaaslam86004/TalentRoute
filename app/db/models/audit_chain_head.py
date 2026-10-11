from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.future_features.audit_integrity import GENESIS_HASH


class AuditChainHead(Base):
    """Locked per-tenant head row that serializes event sequence allocation."""

    __tablename__ = "audit_chain_heads"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=GENESIS_HASH, server_default=GENESIS_HASH
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
