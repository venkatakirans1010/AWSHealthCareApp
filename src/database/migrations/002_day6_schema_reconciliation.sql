-- Non-destructive Day 6 reconciliation for databases initialized from an older schema.
BEGIN;

ALTER TABLE claims
    ADD COLUMN IF NOT EXISTS document_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'RECEIVED',
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE claims ALTER COLUMN patient_id TYPE VARCHAR(64);
ALTER TABLE claims ALTER COLUMN provider_id TYPE VARCHAR(64);
ALTER TABLE claims ALTER COLUMN payer_id TYPE VARCHAR(64);
ALTER TABLE claims ALTER COLUMN claim_type TYPE VARCHAR(64);

CREATE SEQUENCE IF NOT EXISTS claim_number_seq AS BIGINT START WITH 10001 MINVALUE 10001;
SELECT setval(
    'claim_number_seq',
    GREATEST(
        10000,
        COALESCE((SELECT max(substring(claim_id FROM 4)::BIGINT) FROM claims WHERE claim_id ~ '^CLM[0-9]+$'), 10000),
        (SELECT last_value FROM claim_number_seq)
    )
);

ALTER TABLE claims ALTER COLUMN claim_id SET DEFAULT ('CLM' || nextval('claim_number_seq'));
CREATE INDEX IF NOT EXISTS ix_claims_status ON claims (status);
CREATE INDEX IF NOT EXISTS ix_claims_created_at ON claims (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_claim_documents_claim_id ON claim_documents (claim_id);
CREATE INDEX IF NOT EXISTS ix_claim_extractions_claim_id ON claim_extractions (claim_id);
CREATE INDEX IF NOT EXISTS ix_claim_decisions_claim_id ON claim_decisions (claim_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_claim_id ON audit_events (claim_id, created_at);

CREATE OR REPLACE TRIGGER trg_claims_timestamps
    BEFORE UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION set_claim_timestamps();

COMMIT;
