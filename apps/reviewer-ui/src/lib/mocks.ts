// Synthetic mock data for local development without a running API.
// Toggle via NEXT_PUBLIC_USE_MOCKS=true.

import type {
  ComparisonItem,
  Container,
  ContainerDocument,
  ContainerMappingEntry,
  DetectorComparisonReport,
  EntityTypeComparison,
  Job,
  Report,
  ReviewEvent,
} from "./types";

const NOW = "2026-04-30T12:00:00Z";
const HOUR_AGO = "2026-04-30T11:00:00Z";
const TWO_HOURS_AGO = "2026-04-30T10:00:00Z";

export const MOCK_JOBS: Job[] = [
  {
    job_id: "11111111-1111-1111-1111-111111111111",
    status: "awaiting_review",
    decision: "manual_review",
    risk_level: "high",
    risk_score: 45,
    file_format: "docx",
    file_hash: "a".repeat(64),
    file_size: 12345,
    source_filename: "synthetic_contract.docx",
    created_at: NOW,
    updated_at: NOW,
    completed_at: NOW,
    error_message: null,
    mode: "anonymization",
  },
  {
    job_id: "22222222-2222-2222-2222-222222222222",
    status: "auto_approved",
    decision: "auto_approve",
    risk_level: "low",
    risk_score: 0,
    file_format: "txt",
    file_hash: "b".repeat(64),
    file_size: 256,
    source_filename: "memo_clean.txt",
    created_at: HOUR_AGO,
    updated_at: HOUR_AGO,
    completed_at: HOUR_AGO,
    error_message: null,
    mode: "anonymization",
  },
  {
    job_id: "33333333-3333-3333-3333-333333333333",
    status: "awaiting_review",
    decision: "manual_review",
    risk_level: "critical",
    risk_score: 120,
    file_format: "pdf",
    file_hash: "c".repeat(64),
    file_size: 50_000,
    source_filename: "leaked_token.pdf",
    created_at: TWO_HOURS_AGO,
    updated_at: TWO_HOURS_AGO,
    completed_at: TWO_HOURS_AGO,
    error_message: null,
    mode: "anonymization",
  },
  {
    job_id: "44444444-4444-4444-4444-444444444444",
    status: "approved",
    decision: "sample_review",
    risk_level: "medium",
    risk_score: 15,
    file_format: "docx",
    file_hash: "d".repeat(64),
    file_size: 8000,
    source_filename: "meeting_notes.docx",
    created_at: TWO_HOURS_AGO,
    updated_at: HOUR_AGO,
    completed_at: HOUR_AGO,
    error_message: null,
    mode: "anonymization",
  },
];

export const MOCK_REPORTS: Record<string, Report> = {
  "11111111-1111-1111-1111-111111111111": {
    risk_assessment: {
      score: 45,
      level: "high",
      decision: "manual_review",
      reasons: [
        "5x private_person (weight=4, contribution=20)",
        "1x cpf (weight=50, contribution=50, validated check digits)",
      ],
    },
    residual_spans: [
      {
        entity_type: "private_person",
        start: 0,
        end: 9,
        confidence: 0.92,
        source: "second_pass",
      },
    ],
    rule_findings: [
      { rule_id: "cpf", start: 80, end: 94, severity: "high" },
    ],
    redacted_text:
      "[PESSOA_01] signed the contract on [DATA_01] at 42 [ENDERECO_01], Test City. " +
      "His CPF is 111.444.777-35 and email is [EMAIL_01]. " +
      "Phone: [TELEFONE_01] during business hours.",
    applied_spans: [
      {
        block_id: "block-0000",
        page: null,
        doc_start: 0,
        doc_end: 9,
        local_start: 0,
        local_end: 9,
        entity_type: "private_person",
        strategy: "indexed",
        replacement: "[PESSOA_01]",
      },
      {
        block_id: "block-0000",
        page: null,
        doc_start: 35,
        doc_end: 45,
        local_start: 35,
        local_end: 45,
        entity_type: "private_date",
        strategy: "indexed",
        replacement: "[DATA_01]",
      },
      {
        block_id: "block-0000",
        page: null,
        doc_start: 49,
        doc_end: 58,
        local_start: 49,
        local_end: 58,
        entity_type: "private_address",
        strategy: "indexed",
        replacement: "[ENDERECO_01]",
      },
      {
        block_id: "block-0000",
        page: null,
        doc_start: 110,
        doc_end: 117,
        local_start: 110,
        local_end: 117,
        entity_type: "private_email",
        strategy: "indexed",
        replacement: "[EMAIL_01]",
      },
      {
        block_id: "block-0000",
        page: null,
        doc_start: 130,
        doc_end: 143,
        local_start: 130,
        local_end: 143,
        entity_type: "private_phone",
        strategy: "indexed",
        replacement: "[TELEFONE_01]",
      },
    ],
  },
};

export const MOCK_REVIEW_EVENTS: Record<string, ReviewEvent[]> = {};

// ---------------------------------------------------------------------------
// Detector comparison — synthetic mock with one item per status.
// ---------------------------------------------------------------------------

// Synthetic block texts. The offsets in MOCK_COMPARISON_ITEMS index
// directly into these strings — keep them in sync.
const MOCK_BLOCK_0 = "Cliente: Joao Silva\nEmail: alice@example.com\nOAB/SP 12345";
const MOCK_BLOCK_1 = "Endereço: Rua das Flores, 100 — Apto 42";
const MOCK_BLOCK_2 = "111.444.777-35 (CPF validado)";

const MOCK_COMPARISON_ITEMS: ComparisonItem[] = [
  {
    block_id: "block-0000",
    status: "both",
    opf_span: {
      start: MOCK_BLOCK_0.indexOf("Joao Silva"),
      end: MOCK_BLOCK_0.indexOf("Joao Silva") + "Joao Silva".length,
      entity_type: "private_person",
      confidence: 0.93,
      source: "openai_privacy_filter",
      text_preview: "Joao Silva",
    },
    regex_span: {
      start: MOCK_BLOCK_0.indexOf("Joao Silva"),
      end: MOCK_BLOCK_0.indexOf("Joao Silva") + "Joao Silva".length,
      entity_type: "private_person",
      confidence: 0.95,
      source: "br_labeled_name",
      text_preview: "Joao Silva",
    },
    overlap_ratio: 1.0,
    context_preview: "Cliente: ‹…›\nEmail",
  },
  {
    block_id: "block-0000",
    status: "opf_only",
    opf_span: {
      start: MOCK_BLOCK_0.indexOf("alice@example.com"),
      end:
        MOCK_BLOCK_0.indexOf("alice@example.com") +
        "alice@example.com".length,
      entity_type: "private_email",
      confidence: 0.91,
      source: "openai_privacy_filter",
      text_preview: "alice@example.com",
    },
    regex_span: null,
    overlap_ratio: 0,
    context_preview: "Email: ‹…›\nOAB/SP",
  },
  {
    block_id: "block-0000",
    status: "regex_only",
    opf_span: null,
    regex_span: {
      start: MOCK_BLOCK_0.indexOf("OAB/SP 12345"),
      end:
        MOCK_BLOCK_0.indexOf("OAB/SP 12345") + "OAB/SP 12345".length,
      entity_type: "oab",
      confidence: 0.95,
      source: "br_oab",
      text_preview: "OAB/SP 12345",
    },
    overlap_ratio: 0,
    context_preview: "example.com\n‹…›",
  },
  {
    block_id: "block-0001",
    status: "partial_overlap",
    opf_span: {
      start: MOCK_BLOCK_1.indexOf("Rua das Flores"),
      end:
        MOCK_BLOCK_1.indexOf("Rua das Flores") + "Rua das Flores, 100".length,
      entity_type: "private_address",
      confidence: 0.78,
      source: "openai_privacy_filter",
      text_preview: "Rua das Flores, 100",
    },
    regex_span: {
      start: MOCK_BLOCK_1.indexOf("das Flores"),
      end:
        MOCK_BLOCK_1.indexOf("das Flores") + "das Flores, 100 — Apto 42".length,
      entity_type: "private_address",
      confidence: 0.92,
      source: "br_logradouro",
      text_preview: "das Flores, 100 — Apto 42",
    },
    overlap_ratio: 0.65,
    context_preview: "Endereço: ‹…›",
  },
  {
    block_id: "block-0002",
    status: "type_conflict",
    opf_span: {
      start: 0,
      end: 14,
      entity_type: "account_number",
      confidence: 0.81,
      source: "openai_privacy_filter",
      text_preview: "111.444.777-35",
    },
    regex_span: {
      start: 0,
      end: 14,
      entity_type: "cpf",
      confidence: 0.99,
      source: "br_cpf",
      text_preview: "111.444.777-35",
    },
    overlap_ratio: 1.0,
    context_preview: "‹…› (CPF validado)",
  },
];

const MOCK_BY_TYPE: EntityTypeComparison[] = [
  {
    entity_type: "cpf",
    summary: {
      total: 1,
      both: 0,
      opf_only: 0,
      regex_only: 0,
      partial_overlap: 0,
      type_conflict: 1,
    },
  },
  {
    entity_type: "oab",
    summary: {
      total: 1,
      both: 0,
      opf_only: 0,
      regex_only: 1,
      partial_overlap: 0,
      type_conflict: 0,
    },
  },
  {
    entity_type: "private_address",
    summary: {
      total: 1,
      both: 0,
      opf_only: 0,
      regex_only: 0,
      partial_overlap: 1,
      type_conflict: 0,
    },
  },
  {
    entity_type: "private_email",
    summary: {
      total: 1,
      both: 0,
      opf_only: 1,
      regex_only: 0,
      partial_overlap: 0,
      type_conflict: 0,
    },
  },
  {
    entity_type: "private_person",
    summary: {
      total: 1,
      both: 1,
      opf_only: 0,
      regex_only: 0,
      partial_overlap: 0,
      type_conflict: 0,
    },
  },
];

export const MOCK_DETECTOR_COMPARISONS: Record<string, DetectorComparisonReport> = {
  "11111111-1111-1111-1111-111111111111": {
    job_id: "11111111-1111-1111-1111-111111111111",
    summary: {
      total: 5,
      both: 1,
      opf_only: 1,
      regex_only: 1,
      partial_overlap: 1,
      type_conflict: 1,
    },
    by_entity_type: MOCK_BY_TYPE,
    items: MOCK_COMPARISON_ITEMS,
    blocks: [
      { block_id: "block-0000", text: MOCK_BLOCK_0 },
      { block_id: "block-0001", text: MOCK_BLOCK_1 },
      { block_id: "block-0002", text: MOCK_BLOCK_2 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Containers — synthetic workspaces for the offline UI mode.
// ---------------------------------------------------------------------------

export const MOCK_CONTAINERS: Container[] = [
  {
    container_id: "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    name: "Análise Alfa",
    description:
      "Conjunto de documentos da análise Alfa — síntese, depoimentos e laudos.",
    status: "active",
    document_count: 4,
    marker_count: 18,
    created_at: TWO_HOURS_AGO,
    updated_at: HOUR_AGO,
  },
  {
    container_id: "cont-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    name: "Caso Beta — fase de coleta",
    description: null,
    status: "active",
    document_count: 1,
    marker_count: 3,
    created_at: HOUR_AGO,
    updated_at: NOW,
  },
  {
    container_id: "cont-cccccccc-cccc-cccc-cccc-cccccccccccc",
    name: "Arquivo Gama (encerrado)",
    description: "Container arquivado — mantido para auditoria.",
    status: "archived",
    document_count: 7,
    marker_count: 42,
    created_at: TWO_HOURS_AGO,
    updated_at: TWO_HOURS_AGO,
  },
];

// Container Alfa: a couple of synthetic processed documents and the
// mapping table the UI shows on /containers/{id}/mapping.

export const MOCK_CONTAINER_DOCUMENTS: Record<string, ContainerDocument[]> = {
  "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": [
    {
      document_id: "doc-1111-1111",
      container_id: "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      job_id: "job-1111-1111-1111-111111111111",
      filename: "depoimento_pessoa_a.txt",
      source_type: "raw_sensitive_document",
      role: "source",
      status: "ready",
      file_format: "txt",
      file_hash: "a".repeat(64),
      file_size: 4_512,
      error_message: null,
      created_at: HOUR_AGO,
      updated_at: HOUR_AGO,
    },
    {
      document_id: "doc-2222-2222",
      container_id: "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      job_id: "job-2222-2222-2222-222222222222",
      filename: "laudo_tecnico_v2.docx",
      source_type: "raw_sensitive_document",
      role: "report",
      status: "pending_review",
      file_format: "docx",
      file_hash: "b".repeat(64),
      file_size: 18_320,
      error_message: null,
      created_at: NOW,
      updated_at: NOW,
    },
  ],
};

export const MOCK_CONTAINER_MAPPING: Record<string, ContainerMappingEntry[]> = {
  "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": [
    {
      id: 1,
      container_id: "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      entity_type: "private_person",
      marker: "[PESSOA_0001]",
      original_text: "Joao Silva",
      normalized_value: "joao silva",
      review_status: "auto",
      detection_source: "openai_privacy_filter",
      created_from_document_id: "doc-1111-1111",
      first_seen_at: HOUR_AGO,
      last_seen_at: NOW,
      occurrences: [
        { document_id: "doc-1111-1111", filename: "depoimento_pessoa_a.txt" },
        { document_id: "doc-2222-2222", filename: "laudo_tecnico_v2.docx" },
      ],
    },
    {
      id: 2,
      container_id: "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      entity_type: "cpf",
      marker: "[CPF_0001]",
      original_text: "111.444.777-35",
      normalized_value: "11144477735",
      review_status: "auto",
      detection_source: "br_cpf",
      created_from_document_id: "doc-1111-1111",
      first_seen_at: HOUR_AGO,
      last_seen_at: HOUR_AGO,
      occurrences: [
        { document_id: "doc-1111-1111", filename: "depoimento_pessoa_a.txt" },
      ],
    },
    {
      id: 3,
      container_id: "cont-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      entity_type: "private_email",
      marker: "[EMAIL_0001]",
      original_text: "joao@example.com",
      normalized_value: "joao@example.com",
      review_status: "auto",
      detection_source: "openai_privacy_filter",
      created_from_document_id: "doc-1111-1111",
      first_seen_at: HOUR_AGO,
      last_seen_at: NOW,
      occurrences: [
        { document_id: "doc-1111-1111", filename: "depoimento_pessoa_a.txt" },
      ],
    },
  ],
};
