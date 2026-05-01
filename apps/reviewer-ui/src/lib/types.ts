// Types mirror the FastAPI response shapes. Keep in sync with src/anonymizer_api/schemas.py.

export type JobStatusValue =
  | "pending"
  | "processing"
  | "awaiting_review"
  | "auto_approved"
  | "approved"
  | "rejected"
  | "failed";

export type JobMode = "anonymization" | "reversible_pseudonymization";

export type Decision =
  | "auto_approve"
  | "sample_review"
  | "manual_review"
  | null;

export type RiskLevel = "low" | "medium" | "high" | "critical" | null;

export interface Job {
  job_id: string;
  status: JobStatusValue;
  mode: JobMode;
  decision: Decision;
  risk_level: RiskLevel;
  risk_score: number | null;
  file_format: string;
  file_hash: string;
  file_size: number;
  source_filename: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  error_message: string | null;
}

// ---------------------------------------------------------------------------
// Reversible workflow
// ---------------------------------------------------------------------------

export interface PlaceholderInfo {
  placeholder: string;
  original_text: string;
  entity_type: string;
  occurrences: number;
}

export interface ReversiblePackage {
  pseudonymized_text: string;
  instructions: string;
  placeholders: PlaceholderInfo[];
}

export interface PlaceholderCount {
  placeholder: string;
  expected: number;
  actual: number;
}

export interface ValidationReport {
  valid: boolean;
  missing: PlaceholderCount[];
  duplicated: PlaceholderCount[];
  unexpected: string[];
}

export interface RestoredResult {
  restored_text: string;
  validation: ValidationReport;
}

export interface ReversibleStatus {
  mode: JobMode;
  available: boolean;
  has_restored: boolean;
  placeholder_count: number;
}

export interface AppliedSpan {
  block_id: string;
  page: number | null;
  doc_start: number;
  doc_end: number;
  local_start: number;
  local_end: number;
  // Authoritative position of the replacement in the current redacted text.
  // Pipeline runs from version 0.2 onwards always populate these. The frontend
  // falls back to delta math on doc_start/doc_end for older payloads.
  redacted_start?: number;
  redacted_end?: number;
  entity_type: string;
  strategy: "replace" | "pseudonym" | "mask" | "suppress" | "indexed";
  replacement: string;
  manual?: boolean;
  // Detection provenance — which detector produced this span.
  // "openai_privacy_filter" → OPF model
  // "br_*" → deterministic regex (br_cpf, br_oab, br_logradouro, ...)
  // "manual" → reviewer-driven via UI
  source?: string | null;
  confidence?: number | null;
  // Original PII value and surrounding context, captured at detection time.
  // Lets the reviewer judge whether the substitution is correct or a false
  // positive that should be reverted.
  original_text?: string;
  original_context_before?: string;
  original_context_after?: string;
  // True when the reviewer marked the detection as a false positive — the
  // original text was put back in the redacted file at this span's offsets.
  false_positive?: boolean;
  original_replacement?: string;
}

export interface RiskAssessment {
  score: number;
  level: "low" | "medium" | "high" | "critical";
  decision: "auto_approve" | "sample_review" | "manual_review";
  reasons: string[];
}

export interface Report {
  risk_assessment: RiskAssessment;
  residual_spans: Array<{
    entity_type: string;
    start: number;
    end: number;
    confidence: number | null;
    source: string;
  }>;
  rule_findings: Array<{
    rule_id: string;
    start: number;
    end: number;
    severity: string;
  }>;
  redacted_text?: string;
  applied_spans?: AppliedSpan[];
}

export type ReviewEventType =
  | "accept"
  | "edit"
  | "false_positive"
  | "missed_pii"
  | "comment";

export interface ReviewEventInput {
  event_type: ReviewEventType;
  span_index?: number | null;
  reviewer?: string | null;
  note?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface ReviewEvent {
  id: number;
  event_type: string;
  span_index: number | null;
  reviewer: string | null;
  note: string | null;
  payload: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Detector comparison (diagnostic OPF vs regex mode)
// ---------------------------------------------------------------------------

// Side-of-detection used by the comparison view.
export type DetectorSource = "opf" | "regex";

export type ComparisonStatus =
  | "both"
  | "opf_only"
  | "regex_only"
  | "partial_overlap"
  | "type_conflict";

export interface DetectorSpanView {
  start: number;
  end: number;
  entity_type: string;
  confidence: number | null;
  source: string | null;
  text_preview: string | null;
}

export interface ComparisonItem {
  block_id: string;
  status: ComparisonStatus;
  opf_span: DetectorSpanView | null;
  regex_span: DetectorSpanView | null;
  overlap_ratio: number;
  context_preview: string | null;
}

export interface ComparisonSummary {
  total: number;
  both: number;
  opf_only: number;
  regex_only: number;
  partial_overlap: number;
  type_conflict: number;
}

export interface EntityTypeComparison {
  entity_type: string;
  summary: ComparisonSummary;
}

export interface ComparisonBlock {
  block_id: string;
  text: string;
}

export interface DetectorComparisonReport {
  job_id: string;
  summary: ComparisonSummary;
  by_entity_type: EntityTypeComparison[];
  items: ComparisonItem[];
  // Optional: blocks of raw text the items reference. When present the
  // UI can render the source with coloured highlights at item offsets.
  blocks?: ComparisonBlock[];
}

// ---------------------------------------------------------------------------
// Pseudonymization containers (Sprint 1: CRUD; Sprint 2 will extend with
// documents and mapping entries).
// ---------------------------------------------------------------------------

export type ContainerStatus = "active" | "archived";

export interface Container {
  container_id: string;
  name: string;
  description: string | null;
  status: ContainerStatus;
  // Aggregate counts. Sprint 1 always reports zero — the fields exist
  // up-front so the table doesn't shift layout when Sprint 2 lands.
  document_count: number;
  marker_count: number;
  created_at: string;
  updated_at: string;
}

export interface ContainerCreateInput {
  name: string;
  description?: string;
}

export interface ContainerUpdateInput {
  name?: string;
  description?: string;
  status?: ContainerStatus;
}

// ---------------------------------------------------------------------------
// Documents and mapping (Sprint 2)
// ---------------------------------------------------------------------------

export type ContainerDocumentSourceType =
  | "raw_sensitive_document"
  | "already_pseudonymized_document";

export type ContainerDocumentStatus =
  | "pending"
  | "processing"
  | "pending_review"
  | "ready"
  | "rejected"
  | "failed";

export interface ContainerDocument {
  document_id: string;
  container_id: string;
  // Backing job that drives the review pipeline. Always set for raw
  // sensitive uploads; null for already-pseudonymized imports.
  job_id: string | null;
  filename: string;
  source_type: ContainerDocumentSourceType;
  role: string;
  status: ContainerDocumentStatus;
  file_format: string;
  file_hash: string;
  file_size: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContainerMappingOccurrence {
  document_id: string;
  filename: string;
}

export interface ContainerMappingEntry {
  id: number;
  container_id: string;
  entity_type: string;
  marker: string;
  original_text: string;
  normalized_value: string;
  review_status: string;
  detection_source: string | null;
  created_from_document_id: string | null;
  first_seen_at: string;
  last_seen_at: string;
  // Documents in this container where the marker was observed.
  occurrences: ContainerMappingOccurrence[];
}

export interface ContainerValidationSummary {
  total_well_formed: number;
  known_markers: string[];
  unknown_markers: string[];
  malformed_markers: string[];
  is_clean: boolean;
}

export interface ContainerRestoreResult {
  restored_text: string;
  replaced_token_count: number;
  replaced_unique_count: number;
  unknown_markers: string[];
  malformed_markers: string[];
  is_clean: boolean;
}

// ---------------------------------------------------------------------------
// Pseudonymized document review (dedicated screen)
// ---------------------------------------------------------------------------

export interface ResidualPiiSpan {
  start: number;
  end: number;
  entity_type: string;
  confidence: number | null;
  detection_source: string | null;
  fragment: string;
  fragment_hash: string;
}

export interface PseudonymizedReviewPayload {
  document_id: string;
  container_id: string;
  status: ContainerDocumentStatus;
  filename: string;
  text: string;
  validation: ContainerValidationSummary;
  residual_pii: ResidualPiiSpan[];
}

export interface PseudonymizedManualRedactionResult {
  marker: string;
  occurrences: number;
  marker_created: boolean;
  validation: ContainerValidationSummary;
}
