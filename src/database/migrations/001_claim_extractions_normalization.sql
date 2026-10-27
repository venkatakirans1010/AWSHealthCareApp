-- Day 8: normalized claim extraction metadata on claim_extractions.
--
-- Idempotent and non-destructive: adds missing columns, a constraint and indexes; never drops anything or rewrites
-- existing values. Apply after schema.sql:
--   Local Docker (new volume): applied automatically as 02-... (docker-compose.yml).
--   Any database:              python scripts/init_db.py (from backend/claims-api), which applies schema.sql and
--                              every file in database/migrations/ in name order, using DATABASE_URL.
--
-- One row per normalization of one raw OCR result: (claim_id, raw_ocr_artifact_key, raw_ocr_artifact_etag) is
-- unique, so retrying the same normalization updates its row instead of adding a duplicate, while a new OCR run
-- (new ETag) gets a new row and the history is kept.

BEGIN;

ALTER TABLE claim_extractions
    ADD COLUMN IF NOT EXISTS review_required         BOOLEAN,
    ADD COLUMN IF NOT EXISTS review_flags            JSONB         NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS raw_ocr_artifact_key    VARCHAR(1024),
    ADD COLUMN IF NOT EXISTS raw_ocr_artifact_etag   VARCHAR(128),
    ADD COLUMN IF NOT EXISTS normalized_artifact_key VARCHAR(1024),
    ADD COLUMN IF NOT EXISTS failure_reason          TEXT,
    ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now();

-- NOT VALID: enforced for every new or updated row; rows written before this migration are not re-checked.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_claim_extractions_status' AND conrelid = 'claim_extractions'::regclass
    ) THEN
        ALTER TABLE claim_extractions
            ADD CONSTRAINT ck_claim_extractions_status
            CHECK (extraction_status IN ('PENDING', 'COMPLETE', 'FAILED', 'REVIEW_REQUIRED')) NOT VALID;
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_extractions_raw_ocr_artifact
    ON claim_extractions (claim_id, raw_ocr_artifact_key, raw_ocr_artifact_etag);

CREATE INDEX IF NOT EXISTS ix_claim_extractions_status ON claim_extractions (extraction_status);

-- Same trigger function as claims: updated_at changes on every UPDATE, created_at never changes.
CREATE OR REPLACE TRIGGER trg_claim_extractions_timestamps
    BEFORE UPDATE ON claim_extractions
    FOR EACH ROW EXECUTE FUNCTION set_claim_timestamps();

COMMIT;