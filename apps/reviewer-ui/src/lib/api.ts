// Thin API client. Switches between real fetch and in-memory mocks via env.

import {
  MOCK_CONTAINER_DOCUMENTS,
  MOCK_CONTAINER_MAPPING,
  MOCK_CONTAINERS,
  MOCK_DETECTOR_COMPARISONS,
  MOCK_JOBS,
  MOCK_REPORTS,
  MOCK_REVIEW_EVENTS,
} from "./mocks";
import type {
  Container,
  ContainerCreateInput,
  ContainerDocument,
  ContainerMappingEntry,
  ContainerRestoreResult,
  ContainerUpdateInput,
  ContainerValidationSummary,
  DetectorComparisonReport,
  Job,
  JobMode,
  OpfStatus,
  PseudonymizedManualRedactionResult,
  PseudonymizedReviewPayload,
  Report,
  RestoredResult,
  ReversiblePackage,
  ReversibleStatus,
  ReviewEvent,
  ReviewEventInput,
  ValidationReport,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:9000";
const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true";

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface SettingsCatalogue {
  enabled_detectors: string[];
  available_detectors: string[];
}

export async function getSettings(): Promise<SettingsCatalogue> {
  if (USE_MOCKS) {
    return {
      enabled_detectors: ["cpf", "cnpj", "private_email", "private_person"],
      available_detectors: ["cpf", "cnpj", "private_email", "private_person"],
    };
  }
  return fetchJSON<SettingsCatalogue>(`${BASE}/settings`);
}

export async function updateSettings(
  enabled: string[]
): Promise<SettingsCatalogue> {
  if (USE_MOCKS) {
    return {
      enabled_detectors: enabled,
      available_detectors: enabled,
    };
  }
  return fetchJSON<SettingsCatalogue>(`${BASE}/settings`, {
    method: "PUT",
    body: JSON.stringify({ enabled_detectors: enabled }),
  });
}

export async function listJobs(): Promise<Job[]> {
  if (USE_MOCKS) return MOCK_JOBS;
  return fetchJSON<Job[]>(`${BASE}/jobs`);
}

export async function uploadJob(
  file: File,
  mode: JobMode = "anonymization"
): Promise<{ job_id: string; status: string; created_at: string }> {
  if (USE_MOCKS) {
    const id = `mock-${Date.now()}`;
    const newJob: Job = {
      job_id: id,
      status: "processing",
      mode,
      decision: null,
      risk_level: null,
      risk_score: null,
      file_format: file.name.split(".").pop() ?? "",
      file_hash: "0".repeat(64),
      file_size: file.size,
      source_filename: file.name,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      completed_at: null,
      error_message: null,
    };
    MOCK_JOBS.unshift(newJob);
    return { job_id: id, status: "processing", created_at: newJob.created_at };
  }
  // Note: don't set Content-Type — the browser must set it with the multipart boundary.
  const formData = new FormData();
  formData.append("file", file);
  formData.append("mode", mode);
  const res = await fetch(`${BASE}/jobs/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Reversible workflow
// ---------------------------------------------------------------------------

export async function buildReversiblePackage(
  jobId: string
): Promise<ReversiblePackage> {
  return fetchJSON<ReversiblePackage>(
    `${BASE}/jobs/${jobId}/reversible/package`,
    { method: "POST" }
  );
}

export async function validateProcessedText(
  jobId: string,
  processedText: string
): Promise<ValidationReport> {
  return fetchJSON<ValidationReport>(
    `${BASE}/jobs/${jobId}/reversible/validate`,
    {
      method: "POST",
      body: JSON.stringify({ processed_text: processedText }),
    }
  );
}

export async function restoreProcessedText(
  jobId: string,
  processedText: string
): Promise<RestoredResult> {
  return fetchJSON<RestoredResult>(
    `${BASE}/jobs/${jobId}/reversible/restore`,
    {
      method: "POST",
      body: JSON.stringify({ processed_text: processedText }),
    }
  );
}

export async function getReversibleStatus(
  jobId: string
): Promise<ReversibleStatus> {
  return fetchJSON<ReversibleStatus>(
    `${BASE}/jobs/${jobId}/reversible/status`
  );
}

export function reversibleDownloadUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/reversible/download`;
}

export async function getJob(jobId: string): Promise<Job> {
  if (USE_MOCKS) {
    const job = MOCK_JOBS.find((j) => j.job_id === jobId);
    if (!job) throw new Error("Job not found");
    return job;
  }
  return fetchJSON<Job>(`${BASE}/jobs/${jobId}`);
}

export async function getReport(jobId: string): Promise<Report> {
  if (USE_MOCKS) {
    const r = MOCK_REPORTS[jobId];
    if (!r) throw new Error("Report not found");
    return r;
  }
  return fetchJSON<Report>(`${BASE}/jobs/${jobId}/report`);
}

export async function postReviewEvent(
  jobId: string,
  body: ReviewEventInput
): Promise<ReviewEvent> {
  if (USE_MOCKS) {
    const event: ReviewEvent = {
      id: Math.floor(Math.random() * 1_000_000),
      event_type: body.event_type,
      span_index: body.span_index ?? null,
      reviewer: body.reviewer ?? null,
      note: body.note ?? null,
      payload: body.payload ? JSON.stringify(body.payload) : null,
      created_at: new Date().toISOString(),
    };
    MOCK_REVIEW_EVENTS[jobId] ??= [];
    MOCK_REVIEW_EVENTS[jobId].push(event);
    return event;
  }
  return fetchJSON<ReviewEvent>(`${BASE}/jobs/${jobId}/review-events`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listReviewEvents(jobId: string): Promise<ReviewEvent[]> {
  if (USE_MOCKS) return MOCK_REVIEW_EVENTS[jobId] ?? [];
  return fetchJSON<ReviewEvent[]>(`${BASE}/jobs/${jobId}/review-events`);
}

export async function approveJob(
  jobId: string,
  reviewer?: string,
  note?: string
): Promise<Job> {
  if (USE_MOCKS) {
    const job = MOCK_JOBS.find((j) => j.job_id === jobId);
    if (!job) throw new Error("Job not found");
    job.status = "approved";
    return job;
  }
  return fetchJSON<Job>(`${BASE}/jobs/${jobId}/approve`, {
    method: "POST",
    body: JSON.stringify({ reviewer: reviewer || null, note: note ?? null }),
  });
}

export async function rejectJob(
  jobId: string,
  reviewer?: string,
  note?: string
): Promise<Job> {
  if (USE_MOCKS) {
    const job = MOCK_JOBS.find((j) => j.job_id === jobId);
    if (!job) throw new Error("Job not found");
    job.status = "rejected";
    return job;
  }
  return fetchJSON<Job>(`${BASE}/jobs/${jobId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reviewer: reviewer || null, note: note ?? null }),
  });
}

export async function unapproveJob(
  jobId: string,
  reviewer?: string,
  note?: string
): Promise<Job> {
  if (USE_MOCKS) {
    const job = MOCK_JOBS.find((j) => j.job_id === jobId);
    if (!job) throw new Error("Job not found");
    job.status = "awaiting_review";
    return job;
  }
  return fetchJSON<Job>(`${BASE}/jobs/${jobId}/unapprove`, {
    method: "POST",
    body: JSON.stringify({ reviewer: reviewer || null, note: note ?? null }),
  });
}

export async function revertSpan(
  jobId: string,
  spanIndex: number,
  reviewer?: string,
  note?: string
): Promise<Report> {
  if (USE_MOCKS) {
    const r = MOCK_REPORTS[jobId];
    if (!r) throw new Error("Report not found");
    return r;
  }
  return fetchJSON<Report>(
    `${BASE}/jobs/${jobId}/spans/${spanIndex}/revert`,
    {
      method: "POST",
      body: JSON.stringify({
        reviewer: reviewer ?? null,
        note: note ?? null,
      }),
    }
  );
}

export async function applyManualRedaction(
  jobId: string,
  start: number,
  end: number,
  entityType: string,
  expectedText?: string,
  reviewer?: string,
  note?: string
): Promise<Report & { manual_redaction_occurrences?: number }> {
  if (USE_MOCKS) {
    // Best-effort mock implementation: mutates the in-memory report.
    const r = MOCK_REPORTS[jobId];
    if (!r || !r.redacted_text) throw new Error("Report not found in mocks");
    const replacement = `[MANUAL_${(r.applied_spans?.length ?? 0) + 1}]`;
    r.redacted_text =
      r.redacted_text.slice(0, start) + replacement + r.redacted_text.slice(end);
    return r;
  }
  return fetchJSON<Report & { manual_redaction_occurrences?: number }>(
    `${BASE}/jobs/${jobId}/manual-redactions`,
    {
      method: "POST",
      body: JSON.stringify({
        start,
        end,
        entity_type: entityType,
        expected_text: expectedText ?? null,
        reviewer: reviewer ?? null,
        note: note ?? null,
      }),
    }
  );
}

export async function deleteJob(jobId: string): Promise<void> {
  if (USE_MOCKS) {
    const idx = MOCK_JOBS.findIndex((j) => j.job_id === jobId);
    if (idx >= 0) MOCK_JOBS.splice(idx, 1);
    return;
  }
  const res = await fetch(`${BASE}/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
}

export function downloadUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/download`;
}

// ---------------------------------------------------------------------------
// Detector comparison (diagnostic OPF-vs-regex mode — does not change the job)
// ---------------------------------------------------------------------------

export async function runDetectorComparison(
  jobId: string
): Promise<DetectorComparisonReport> {
  if (USE_MOCKS) {
    const cached = MOCK_DETECTOR_COMPARISONS[jobId];
    if (cached) return cached;
    throw new Error("Comparação não disponível para este job no modo mock.");
  }
  return fetchJSON<DetectorComparisonReport>(
    `${BASE}/jobs/${jobId}/detector-comparison`,
    { method: "POST" }
  );
}

export async function getDetectorComparison(
  jobId: string
): Promise<DetectorComparisonReport | null> {
  if (USE_MOCKS) {
    return MOCK_DETECTOR_COMPARISONS[jobId] ?? null;
  }
  const res = await fetch(`${BASE}/jobs/${jobId}/detector-comparison`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as DetectorComparisonReport;
}

// ---------------------------------------------------------------------------
// Pseudonymization containers (Sprint 1: CRUD)
// ---------------------------------------------------------------------------

export async function listContainers(): Promise<Container[]> {
  if (USE_MOCKS) return [...MOCK_CONTAINERS];
  return fetchJSON<Container[]>(`${BASE}/api/containers`);
}

export async function getContainer(containerId: string): Promise<Container> {
  if (USE_MOCKS) {
    const c = MOCK_CONTAINERS.find((x) => x.container_id === containerId);
    if (!c) throw new Error("Container not found");
    return c;
  }
  return fetchJSON<Container>(`${BASE}/api/containers/${containerId}`);
}

export async function createContainer(
  input: ContainerCreateInput
): Promise<Container> {
  if (USE_MOCKS) {
    const now = new Date().toISOString();
    const created: Container = {
      container_id: `mock-${Date.now()}`,
      name: input.name,
      description: input.description?.trim() || null,
      status: "active",
      document_count: 0,
      marker_count: 0,
      created_at: now,
      updated_at: now,
    };
    MOCK_CONTAINERS.unshift(created);
    return created;
  }
  return fetchJSON<Container>(`${BASE}/api/containers`, {
    method: "POST",
    body: JSON.stringify({
      name: input.name,
      description: input.description ?? null,
    }),
  });
}

export async function updateContainer(
  containerId: string,
  input: ContainerUpdateInput
): Promise<Container> {
  if (USE_MOCKS) {
    const c = MOCK_CONTAINERS.find((x) => x.container_id === containerId);
    if (!c) throw new Error("Container not found");
    if (input.name !== undefined) c.name = input.name;
    if (input.description !== undefined)
      c.description = input.description?.trim() || null;
    if (input.status !== undefined) c.status = input.status;
    c.updated_at = new Date().toISOString();
    return c;
  }
  return fetchJSON<Container>(`${BASE}/api/containers/${containerId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteContainer(containerId: string): Promise<void> {
  if (USE_MOCKS) {
    const idx = MOCK_CONTAINERS.findIndex(
      (x) => x.container_id === containerId
    );
    if (idx >= 0) MOCK_CONTAINERS.splice(idx, 1);
    return;
  }
  const res = await fetch(`${BASE}/api/containers/${containerId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
}

// ---------------------------------------------------------------------------
// Container documents (Sprint 2)
// ---------------------------------------------------------------------------

export async function listContainerDocuments(
  containerId: string
): Promise<ContainerDocument[]> {
  if (USE_MOCKS) {
    return (MOCK_CONTAINER_DOCUMENTS[containerId] ?? []).slice();
  }
  return fetchJSON<ContainerDocument[]>(
    `${BASE}/api/containers/${containerId}/documents`
  );
}

export async function getContainerDocument(
  containerId: string,
  documentId: string
): Promise<ContainerDocument> {
  if (USE_MOCKS) {
    const list = MOCK_CONTAINER_DOCUMENTS[containerId] ?? [];
    const doc = list.find((d) => d.document_id === documentId);
    if (!doc) throw new Error("Document not found");
    return doc;
  }
  return fetchJSON<ContainerDocument>(
    `${BASE}/api/containers/${containerId}/documents/${documentId}`
  );
}

export async function uploadRawContainerDocument(
  containerId: string,
  file: File,
  role: string = "source"
): Promise<ContainerDocument> {
  if (USE_MOCKS) {
    const now = new Date().toISOString();
    const ext = file.name.split(".").pop() ?? "txt";
    const doc: ContainerDocument = {
      document_id: `mock-doc-${Date.now()}`,
      container_id: containerId,
      job_id: `mock-job-${Date.now()}`,
      filename: file.name,
      source_type: "raw_sensitive_document",
      role,
      status: "pending_review",
      file_format: ext,
      file_hash: "0".repeat(64),
      file_size: file.size,
      error_message: null,
      created_at: now,
      updated_at: now,
    };
    MOCK_CONTAINER_DOCUMENTS[containerId] ??= [];
    MOCK_CONTAINER_DOCUMENTS[containerId].unshift(doc);
    return doc;
  }
  const formData = new FormData();
  formData.append("file", file);
  formData.append("role", role);
  const res = await fetch(
    `${BASE}/api/containers/${containerId}/documents/raw`,
    { method: "POST", body: formData }
  );
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function deleteContainerDocument(
  containerId: string,
  documentId: string
): Promise<void> {
  if (USE_MOCKS) {
    const list = MOCK_CONTAINER_DOCUMENTS[containerId];
    if (list) {
      const idx = list.findIndex((d) => d.document_id === documentId);
      if (idx >= 0) list.splice(idx, 1);
    }
    return;
  }
  const res = await fetch(
    `${BASE}/api/containers/${containerId}/documents/${documentId}`,
    { method: "DELETE" }
  );
  if (!res.ok && res.status !== 204) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
}

export async function getContainerMapping(
  containerId: string
): Promise<ContainerMappingEntry[]> {
  if (USE_MOCKS) {
    return (MOCK_CONTAINER_MAPPING[containerId] ?? []).slice();
  }
  return fetchJSON<ContainerMappingEntry[]>(
    `${BASE}/api/containers/${containerId}/mapping`
  );
}

// ---------------------------------------------------------------------------
// Sprint 3 — already-pseudonymized documents + XLSX exports
// ---------------------------------------------------------------------------

export async function uploadPseudonymizedContainerDocument(
  containerId: string,
  file: File,
  role: string = "edited_version"
): Promise<ContainerDocument> {
  if (USE_MOCKS) {
    const now = new Date().toISOString();
    const ext = file.name.split(".").pop() ?? "txt";
    const doc: ContainerDocument = {
      document_id: `mock-pseudo-${Date.now()}`,
      container_id: containerId,
      // Already-pseudonymized imports skip the review pipeline.
      job_id: null,
      filename: file.name,
      source_type: "already_pseudonymized_document",
      role,
      status: "ready",
      file_format: ext,
      file_hash: "0".repeat(64),
      file_size: file.size,
      error_message: null,
      created_at: now,
      updated_at: now,
    };
    MOCK_CONTAINER_DOCUMENTS[containerId] ??= [];
    MOCK_CONTAINER_DOCUMENTS[containerId].unshift(doc);
    return doc;
  }
  const formData = new FormData();
  formData.append("file", file);
  formData.append("role", role);
  const res = await fetch(
    `${BASE}/api/containers/${containerId}/documents/pseudonymized`,
    { method: "POST", body: formData }
  );
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function validatePseudonymizedText(
  containerId: string,
  processedText: string
): Promise<ContainerValidationSummary> {
  if (USE_MOCKS) {
    return {
      total_well_formed: 0,
      known_markers: [],
      unknown_markers: [],
      malformed_markers: [],
      is_clean: true,
    };
  }
  return fetchJSON<ContainerValidationSummary>(
    `${BASE}/api/containers/${containerId}/validate-pseudonymized`,
    {
      method: "POST",
      body: JSON.stringify({ processed_text: processedText }),
    }
  );
}

export function containerMappingExportSensitiveUrl(
  containerId: string
): string {
  return `${BASE}/api/containers/${containerId}/mapping/export-sensitive.xlsx`;
}

// Direct-download URLs (browser handles the Content-Disposition).
export function containerDocumentDownloadUrl(
  containerId: string,
  documentId: string
): string {
  return `${BASE}/api/containers/${containerId}/documents/${documentId}/download`;
}

export function containerBundleDownloadUrl(containerId: string): string {
  return `${BASE}/api/containers/${containerId}/download-bundle.zip`;
}

// ---------------------------------------------------------------------------
// Pseudonymized document review
// ---------------------------------------------------------------------------

export async function getPseudonymizedReview(
  containerId: string,
  documentId: string
): Promise<PseudonymizedReviewPayload> {
  return fetchJSON<PseudonymizedReviewPayload>(
    `${BASE}/api/containers/${containerId}/documents/${documentId}/review-pseudonymized`
  );
}

export async function approvePseudonymizedDocument(
  containerId: string,
  documentId: string
): Promise<ContainerDocument> {
  return fetchJSON<ContainerDocument>(
    `${BASE}/api/containers/${containerId}/documents/${documentId}/approve-pseudonymized`,
    { method: "POST" }
  );
}

export async function rejectPseudonymizedDocument(
  containerId: string,
  documentId: string
): Promise<ContainerDocument> {
  return fetchJSON<ContainerDocument>(
    `${BASE}/api/containers/${containerId}/documents/${documentId}/reject-pseudonymized`,
    { method: "POST" }
  );
}

export async function applyPseudonymizedManualRedaction(
  containerId: string,
  documentId: string,
  fragment: string,
  entityType: string
): Promise<PseudonymizedManualRedactionResult> {
  return fetchJSON<PseudonymizedManualRedactionResult>(
    `${BASE}/api/containers/${containerId}/documents/${documentId}/manual-redaction-pseudonymized`,
    {
      method: "POST",
      body: JSON.stringify({
        fragment,
        entity_type: entityType,
      }),
    }
  );
}

// ---------------------------------------------------------------------------
// Sprint 4 — Restoration
// ---------------------------------------------------------------------------

export async function restoreContainerText(
  containerId: string,
  processedText: string
): Promise<ContainerRestoreResult> {
  if (USE_MOCKS) {
    const entries = MOCK_CONTAINER_MAPPING[containerId] ?? [];
    let restored = processedText;
    let tokens = 0;
    let unique = 0;
    const unknown: string[] = [];
    const seen = new Set<string>();
    const matches = processedText.match(/\[[A-Z][A-Z_]*_\d{2,}\]/g) ?? [];
    for (const m of matches) {
      if (seen.has(m)) continue;
      seen.add(m);
      const entry = entries.find((e) => e.marker === m);
      if (entry) {
        const count = restored.split(m).length - 1;
        restored = restored.split(m).join(entry.original_text);
        tokens += count;
        unique += 1;
      } else {
        unknown.push(m);
      }
    }
    return {
      restored_text: restored,
      replaced_token_count: tokens,
      replaced_unique_count: unique,
      unknown_markers: unknown,
      malformed_markers: [],
      is_clean: unknown.length === 0,
    };
  }
  return fetchJSON<ContainerRestoreResult>(
    `${BASE}/api/containers/${containerId}/restore/text`,
    {
      method: "POST",
      body: JSON.stringify({ processed_text: processedText }),
    }
  );
}

export async function restoreContainerDocument(
  containerId: string,
  documentId: string
): Promise<ContainerRestoreResult> {
  if (USE_MOCKS) {
    return {
      restored_text: "(modo mock — restauração de documento indisponível)",
      replaced_token_count: 0,
      replaced_unique_count: 0,
      unknown_markers: [],
      malformed_markers: [],
      is_clean: true,
    };
  }
  return fetchJSON<ContainerRestoreResult>(
    `${BASE}/api/containers/${containerId}/restore/document/${documentId}`,
    { method: "POST" }
  );
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

export function recommendedAction(job: Job): {
  label: string;
  href?: string;
  download?: boolean;
} {
  switch (job.status) {
    case "auto_approved":
    case "approved":
      return { label: "Baixar", href: downloadUrl(job.job_id), download: true };
    case "awaiting_review":
      return { label: "Revisar", href: `/jobs/${job.job_id}/review` };
    case "rejected":
    case "failed":
      return { label: "Inspecionar", href: `/jobs/${job.job_id}` };
    case "pending":
    case "processing":
      return { label: "Processando…" };
    default:
      // Defensive fallback for unknown statuses (e.g. legacy "blocked" rows
      // left in older SQLite databases). Treat them as inspectable.
      return { label: "Inspecionar", href: `/jobs/${job.job_id}` };
  }
}

// ---------------------------------------------------------------------------
// OPF runtime toggle
// ---------------------------------------------------------------------------

const MOCK_OPF_STATUS: OpfStatus = {
  available: false,
  enabled: false,
  loading: false,
  error: null,
  in_flight_jobs: 0,
};

export async function getOpfStatus(): Promise<OpfStatus> {
  if (USE_MOCKS) return MOCK_OPF_STATUS;
  return fetchJSON<OpfStatus>(`${BASE}/api/opf/status`);
}

export async function enableOpf(): Promise<OpfStatus> {
  if (USE_MOCKS) return MOCK_OPF_STATUS;
  return fetchJSON<OpfStatus>(`${BASE}/api/opf/enable`, { method: "POST" });
}

export async function disableOpf(): Promise<OpfStatus> {
  if (USE_MOCKS) return MOCK_OPF_STATUS;
  return fetchJSON<OpfStatus>(`${BASE}/api/opf/disable`, { method: "POST" });
}
