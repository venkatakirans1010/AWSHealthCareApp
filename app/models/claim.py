from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

JsonType = JSON().with_variant(JSONB, "postgresql")


class Claim(Base):
    __tablename__ = "claims"
    claim_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="RECEIVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    documents: Mapped[list["ClaimDocument"]] = relationship(back_populates="claim", lazy="selectin")
    __table_args__ = (Index("ix_claims_status", "status"),)


class ClaimDocument(Base):
    __tablename__ = "claim_documents"
    id: Mapped[int] = mapped_column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    s3_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    claim: Mapped[Claim] = relationship(back_populates="documents")


class ClaimExtraction(Base):
    __tablename__ = "claim_extractions"
    id: Mapped[int] = mapped_column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False, index=True)
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    review_required: Mapped[bool | None] = mapped_column(Boolean)
    review_flags: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, nullable=False, default=list)
    raw_ocr_artifact_key: Mapped[str | None] = mapped_column(String(1024))
    raw_ocr_artifact_etag: Mapped[str | None] = mapped_column(String(128))
    normalized_artifact_key: Mapped[str | None] = mapped_column(String(1024))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ClaimDecision(Base):
    __tablename__ = "claim_decisions"
    id: Mapped[int] = mapped_column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list[Any] | dict[str, Any]] = mapped_column(JsonType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
