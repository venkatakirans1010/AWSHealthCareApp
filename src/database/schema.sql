-- Healthcare Claims AI: PostgreSQL schema (PostgreSQL 14 or later; Amazon RDS for PostgreSQL works the same way).
--
-- This file is idempotent and non-destructive: it only creates objects that do not exist yet and never drops
-- tables or deletes rows, so it is safe to run again against a database that already holds claims.
--   Local Docker: applied automatically on the first start of the postgres service (docker-compose.yml).
--   Any database: python scripts/init_db.py (from backend/claims-api), using DATABASE_URL.
-- Later changes go into numbered files in database/migrations/ (ALTER TABLE ... ADD COLUMN IF NOT EXISTS, ...).

BEGIN;

-- Claim numbers. nextval() is atomic, so concurrent requests never get the same number, and numbers are never
-- reused (a rolled-back transaction leaves a gap), which also keeps S3 keys incoming/{claim_id}/ unique.
CREATE SEQUENCE IF NOT EXISTS claim_number_seq AS BIGINT START WITH 10001 MINVALUE 10001;

CREATE TABLE IF NOT EXISTS claims (
    claim_id      VARCHAR(20)  PRIMARY KEY DEFAULT ('CLM' || nextval('claim_number_seq')),
    patient_id    VARCHAR(64)  NOT NULL,
    provider_id   VARCHAR(64)  NOT NULL,
    payer_id      VARCHAR(64)  NOT NULL,
    claim_type    VARCHAR(64)  NOT NULL,
    document_name VARCHAR(255) NOT NULL,
    status        VARCHAR(32)  NOT NULL DEFAULT 'RECEIVED',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_claims_claim_id_format CHECK (claim_id ~ '^CLM[0-9]+$'),
    CONSTRAINT ck_claims_claim_type CHECK (claim_type IN ('INPATIENT', 'OUTPATIENT', 'EMERGENCY', 'PHARMACY')),
    CONSTRAINT ck_claims_status CHECK (
        status IN ('RECEIVED', 'UPLOADED', 'PROCESSING', 'APPROVED', 'DENIED', 'REQUEST_INFORMATION', 'FRAUD_REVIEW', 'FAILED')
    )
);

CREATE INDEX IF NOT EXISTS ix_claims_status ON claims (status);
CREATE INDEX IF NOT EXISTS ix_claims_created_at ON claims (created_at DESC);

-- updated_at changes on every UPDATE; created_at can never be changed after the insert.
CREATE OR REPLACE FUNCTION set_claim_timestamps() RETURNS trigger AS $$
BEGIN
    NEW.created_at := OLD.created_at;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_claims_timestamps
    BEFORE UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION set_claim_timestamps();

-- Documents stored in S3. The object key is metadata for the private bucket, not a download link.
CREATE TABLE IF NOT EXISTS claim_documents (
    id            BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id      VARCHAR(20)   NOT NULL,
    filename      VARCHAR(255)  NOT NULL,
    s3_bucket     VARCHAR(63)   NOT NULL,
    s3_object_key VARCHAR(1024) NOT NULL,
    content_type  VARCHAR(100)  NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT fk_claim_documents_claim FOREIGN KEY (claim_id) REFERENCES claims (claim_id),
    CONSTRAINT uq_claim_documents_s3_object UNIQUE (s3_bucket, s3_object_key)
);

CREATE INDEX IF NOT EXISTS ix_claim_documents_claim_id ON claim_documents (claim_id);

-- Output of document extraction (OCR), one row per extraction attempt. Normalization columns, the status check and
-- the retry key are added by database/migrations/001_claim_extractions_normalization.sql.
CREATE TABLE IF NOT EXISTS claim_extractions (
    id                BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id          VARCHAR(20) NOT NULL,
    extracted_data    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    extraction_status VARCHAR(32) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_claim_extractions_claim FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
);

CREATE INDEX IF NOT EXISTS ix_claim_extractions_claim_id ON claim_extractions (claim_id);

-- Adjudication decisions with the evidence they were based on.
CREATE TABLE IF NOT EXISTS claim_decisions (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id   VARCHAR(20) NOT NULL,
    decision   VARCHAR(32) NOT NULL,
    reason     TEXT,
    evidence   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_claim_decisions_claim FOREIGN KEY (claim_id) REFERENCES claims (claim_id),
    CONSTRAINT ck_claim_decisions_decision CHECK (decision IN ('APPROVED', 'DENIED', 'REQUEST_INFORMATION', 'FRAUD_REVIEW'))
);

CREATE INDEX IF NOT EXISTS ix_claim_decisions_claim_id ON claim_decisions (claim_id);

-- Append-only history of what happened to each claim. details must not hold documents, secrets or PHI.
CREATE TABLE IF NOT EXISTS audit_events (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id   VARCHAR(20) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    details    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_audit_events_claim FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
);

CREATE INDEX IF NOT EXISTS ix_audit_events_claim_id ON audit_events (claim_id, created_at);

COMMIT;
