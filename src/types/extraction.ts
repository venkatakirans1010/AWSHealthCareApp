export type ExtractionStatus = 'PENDING' | 'COMPLETE' | 'FAILED' | 'REVIEW_REQUIRED';

export interface OcrEvidence {
  page_number: number;
  text: string;
  confidence: number | null;
}

export interface OcrCandidate {
  raw_value: string;
  normalized_value: string | null;
  evidence: OcrEvidence | null;
}

export interface OcrCandidateField {
  value: string | null;
  found: boolean;
  conflicting: boolean;
  candidates: OcrCandidate[];
}

export interface OcrFieldReview {
  value: string | null;
  found: boolean;
  evidence: OcrEvidence | null;
  confidence: number | null;
  validation_outcome: string;
  review_required: boolean;
  review_reasons: string[];
}

export interface ClaimOcrResponse {
  claim_id: string;
  extraction_status: string;
  page_count: number;
  average_word_confidence: number | null;
  local_output_path: string;
  s3_result_key: string | null;
  candidate_fields: Record<string, OcrCandidateField>;
  field_review: Record<string, OcrFieldReview>;
  document_claim_id: string | null;
  claim_id_matches_document: boolean | null;
  review_required: boolean;
}

export interface ReviewFlag {
  field: string;
  code: string;
  severity: 'WARNING' | 'ERROR';
  message: string;
}

export interface NormalizedExtraction {
  claim_id: string;
  patient_id: string | null;
  patient_name: string | null;
  provider_id: string | null;
  payer_id: string | null;
  policy_id: string | null;
  diagnosis_code: string | null;
  procedure_code: string | null;
  amount: string | null;
  currency: string | null;
  date_of_service: string | null;
  extraction_status: ExtractionStatus;
  review_required: boolean;
  review_flags: ReviewFlag[];
  source_document: { bucket: string; key: string };
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface NormalizationResponse {
  extraction: NormalizedExtraction;
  raw_ocr_artifact_key: string;
  normalized_artifact_key: string;
}

export interface ProcessingRequestOptions {
  signal?: AbortSignal;
}
