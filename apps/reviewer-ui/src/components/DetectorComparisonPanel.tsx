"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  getDetectorComparison,
  runDetectorComparison,
} from "@/lib/api";
import { ComparisonTextView } from "./ComparisonTextView";
import type {
  ComparisonItem,
  ComparisonStatus,
  DetectorComparisonReport,
  DetectorSpanView,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Static labels and colour mappings — paleta:
//   OPF     → azul (--accent)
//   Regex   → roxo (--purple)
//   Ambos   → verde (--green)
//   Conflito → laranja (--orange)
//   Parcial  → amarelo (--yellow)
// ---------------------------------------------------------------------------

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  both: "Ambos",
  opf_only: "Só OPF",
  regex_only: "Só Regex",
  partial_overlap: "Sobreposição parcial",
  type_conflict: "Conflito de tipo",
};

const STATUS_ICON: Record<ComparisonStatus, string> = {
  both: "🟢",
  opf_only: "🔵",
  regex_only: "🟣",
  partial_overlap: "🟡",
  type_conflict: "🟠",
};

const STATUS_EXPLANATION: Record<ComparisonStatus, string> = {
  both:
    "Os dois detectores marcaram o mesmo trecho com o mesmo tipo. Sinal mais forte de detecção correta.",
  opf_only:
    "Apenas o modelo OPF marcou este trecho. As regras determinísticas não pegaram. Pode indicar PII que só o modelo reconhece.",
  regex_only:
    "Apenas as regras determinísticas marcaram este trecho. O OPF não pegou. Útil para PII estruturado (CPF, OAB, etc.) que o modelo costuma deixar passar.",
  partial_overlap:
    "Os dois detectores acharam algo na mesma região, mas os limites não coincidem totalmente. Verifique se um dos lados está cortando a PII pela metade.",
  type_conflict:
    "Os dois detectores marcaram o mesmo trecho, mas atribuíram tipos diferentes. Em geral o detector regex (determinístico) ganha — é mais específico (ex.: CPF validado em vez de account_number genérico).",
};

const ALL_STATUSES: ComparisonStatus[] = [
  "both",
  "opf_only",
  "regex_only",
  "partial_overlap",
  "type_conflict",
];

interface CardConfig {
  key: string;
  label: string;
  toneClass: string;
  count: (report: DetectorComparisonReport) => number;
}

function totalOpf(report: DetectorComparisonReport): number {
  // Items that have an OPF span on either side.
  return report.items.filter((it) => it.opf_span !== null).length;
}

function totalRegex(report: DetectorComparisonReport): number {
  return report.items.filter((it) => it.regex_span !== null).length;
}

const CARDS: CardConfig[] = [
  {
    key: "opf",
    label: "OPF",
    toneClass: "card-tone-opf",
    count: totalOpf,
  },
  {
    key: "regex",
    label: "Regex",
    toneClass: "card-tone-regex",
    count: totalRegex,
  },
  {
    key: "both",
    label: "Ambos",
    toneClass: "card-tone-both",
    count: (r) => r.summary.both,
  },
  {
    key: "opf_only",
    label: "Só OPF",
    toneClass: "card-tone-opf",
    count: (r) => r.summary.opf_only,
  },
  {
    key: "regex_only",
    label: "Só Regex",
    toneClass: "card-tone-regex",
    count: (r) => r.summary.regex_only,
  },
  {
    key: "type_conflict",
    label: "Conflitos",
    toneClass: "card-tone-conflict",
    count: (r) => r.summary.type_conflict,
  },
  {
    key: "partial_overlap",
    label: "Sobreposição parcial",
    toneClass: "card-tone-partial",
    count: (r) => r.summary.partial_overlap,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function previewOf(item: ComparisonItem): string {
  return (
    item.opf_span?.text_preview ??
    item.regex_span?.text_preview ??
    item.context_preview ??
    ""
  );
}

function formatRatio(ratio: number): string {
  return `${(ratio * 100).toFixed(0)}%`;
}

function formatConfidence(c: number | null | undefined): string {
  if (c === null || c === undefined) return "—";
  return c.toFixed(2);
}

function formatBlock(item: ComparisonItem): string {
  return item.block_id;
}

function uniqueEntityTypes(items: ComparisonItem[]): string[] {
  const set = new Set<string>();
  for (const it of items) {
    if (it.opf_span) set.add(it.opf_span.entity_type);
    if (it.regex_span) set.add(it.regex_span.entity_type);
  }
  return Array.from(set).sort();
}

function uniqueBlocks(items: ComparisonItem[]): string[] {
  const set = new Set<string>();
  for (const it of items) set.add(it.block_id);
  return Array.from(set).sort();
}

function statusClass(status: ComparisonStatus): string {
  return `dc-status dc-status-${status}`;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SpanCard({
  title,
  span,
  toneClass,
}: {
  title: string;
  span: DetectorSpanView | null;
  toneClass: string;
}) {
  if (!span) {
    return (
      <div className={`dc-side ${toneClass}`}>
        <div className="dc-side-title">{title}</div>
        <div className="muted small">Não detectado</div>
      </div>
    );
  }
  return (
    <div className={`dc-side ${toneClass}`}>
      <div className="dc-side-title">{title}</div>
      <table className="dc-side-table">
        <tbody>
          <tr>
            <th>Tipo</th>
            <td className="mono">{span.entity_type}</td>
          </tr>
          <tr>
            <th>Origem</th>
            <td className="mono small">{span.source ?? "—"}</td>
          </tr>
          <tr>
            <th>Posição</th>
            <td className="mono small">
              [{span.start}:{span.end}]
            </td>
          </tr>
          <tr>
            <th>Confiança</th>
            <td>{formatConfidence(span.confidence)}</td>
          </tr>
          <tr>
            <th>Trecho</th>
            <td className="mono small">{span.text_preview ?? "—"}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function DetailRow({ item }: { item: ComparisonItem }) {
  return (
    <tr className="dc-detail-row">
      <td colSpan={7}>
        <div className="dc-detail">
          <p className="dc-explanation">{STATUS_EXPLANATION[item.status]}</p>

          <div className="dc-detail-row-grid">
            <SpanCard
              title="OPF"
              span={item.opf_span}
              toneClass="card-tone-opf"
            />
            <SpanCard
              title="Regex"
              span={item.regex_span}
              toneClass="card-tone-regex"
            />
          </div>

          <div className="dc-detail-meta">
            <div>
              <div className="muted small">Bloco</div>
              <div className="mono small">{formatBlock(item)}</div>
            </div>
            <div>
              <div className="muted small">Sobreposição</div>
              <div>{formatRatio(item.overlap_ratio)}</div>
            </div>
          </div>

          {item.context_preview && (
            <div className="dc-context">
              <div className="muted small">Contexto</div>
              <div className="mono small dc-context-text">
                {item.context_preview}
              </div>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface PanelProps {
  jobId: string;
  // When true, the panel automatically fires the run as soon as it
  // mounts and there's no saved report yet. Driven by the upload card's
  // "Comparação" mode via ``?autocompare=1``.
  autoRun?: boolean;
}

export function DetectorComparisonPanel({
  jobId,
  autoRun = false,
}: PanelProps) {
  const [report, setReport] = useState<DetectorComparisonReport | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [running, setRunning] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  // One-shot guard — autoRun must fire at most once per panel mount.
  const autoFiredRef = useRef<boolean>(false);

  // Filters
  const [statusFilter, setStatusFilter] = useState<ComparisonStatus | "all">(
    "all"
  );
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [blockFilter, setBlockFilter] = useState<string>("all");
  const [search, setSearch] = useState<string>("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Index currently selected from the highlighted text view (used to
  // visually pulse the corresponding row + open its detail).
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());

  // Initial fetch — try to load a previously saved comparison.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    getDetectorComparison(jobId)
      .then((r) => {
        if (!alive) return;
        setReport(r);
        // Auto-run if the upload card asked for it and there's no
        // pre-existing report. Guarded by autoFiredRef so navigating
        // away/back doesn't re-trigger.
        if (autoRun && !r && !autoFiredRef.current) {
          autoFiredRef.current = true;
          void handleRun();
        }
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, autoRun]);

  async function handleRun() {
    setRunning(true);
    setError(null);
    try {
      const r = await runDetectorComparison(jobId);
      setReport(r);
      setExpanded(new Set());
      setSelectedIndex(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  function toggle(idx: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  function handleHighlightClick(itemIndex: number) {
    setSelectedIndex(itemIndex);
    // Open the detail row for the clicked item and scroll it into view.
    setExpanded((prev) => {
      const next = new Set(prev);
      next.add(itemIndex);
      return next;
    });
    // Clear filters that would hide this item, so the row is reachable.
    if (report) {
      const it = report.items[itemIndex];
      if (it) {
        if (statusFilter !== "all" && statusFilter !== it.status) {
          setStatusFilter("all");
        }
        if (blockFilter !== "all" && blockFilter !== it.block_id) {
          setBlockFilter("all");
        }
      }
    }
    // Defer scroll until after re-render reflects the cleared filters.
    setTimeout(() => {
      const el = rowRefs.current.get(itemIndex);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  }

  const filteredItems = useMemo(() => {
    if (!report) return [];
    const q = search.trim().toLowerCase();
    return report.items.filter((it) => {
      if (statusFilter !== "all" && it.status !== statusFilter) return false;
      if (blockFilter !== "all" && it.block_id !== blockFilter) return false;
      if (typeFilter !== "all") {
        const types = [
          it.opf_span?.entity_type,
          it.regex_span?.entity_type,
        ].filter(Boolean) as string[];
        if (!types.includes(typeFilter)) return false;
      }
      if (q) {
        const haystack = [
          it.opf_span?.text_preview,
          it.regex_span?.text_preview,
          it.context_preview,
          it.opf_span?.entity_type,
          it.regex_span?.entity_type,
          it.block_id,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [report, statusFilter, typeFilter, blockFilter, search]);

  const types = report ? uniqueEntityTypes(report.items) : [];
  const blocks = report ? uniqueBlocks(report.items) : [];

  return (
    <div className="card dc-panel">
      <div className="dc-header">
        <div>
          <h2 style={{ margin: 0 }}>🔬 Comparação de detectores</h2>
          <p className="muted small" style={{ margin: "4px 0 0 0" }}>
            Mostra o que o <strong>OPF</strong> e as{" "}
            <strong>regras determinísticas (regex)</strong> detectam
            separadamente, do mesmo jeito que cada um roda no pipeline de
            produção — OPF com normalização de caixa (para reconhecer
            nomes em ALL-CAPS), regex com o stack BR completo (CPF, CNPJ,
            OAB, endereços, etc.). Modo diagnóstico: não altera o
            documento nem o status do job.
          </p>
        </div>
        <div className="dc-actions">
          <button
            className="btn btn-primary"
            onClick={handleRun}
            disabled={running || loading}
          >
            {running ? "Rodando…" : report ? "Rodar novamente" : "Rodar comparação"}
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            color: "var(--red)",
            background: "var(--red-bg)",
            border: "1px solid var(--red)",
            borderRadius: 6,
            padding: 10,
            marginTop: 12,
          }}
        >
          ❌ {error}
        </div>
      )}

      {loading && !report && (
        <p className="muted" style={{ marginTop: 12 }}>
          Carregando relatório salvo…
        </p>
      )}

      {!loading && !report && !error && (
        <p className="muted small" style={{ marginTop: 12 }}>
          Ainda não foi gerado nenhum relatório de comparação para este
          documento. Clique em <strong>Rodar comparação</strong> para
          gerar um agora.
        </p>
      )}

      {report && (
        <>
          <div className="dc-cards">
            {CARDS.map((c) => (
              <div key={c.key} className={`dc-card ${c.toneClass}`}>
                <div className="dc-card-label">{c.label}</div>
                <div className="dc-card-value">{c.count(report)}</div>
              </div>
            ))}
          </div>

          <div className="dc-filters">
            <label className="dc-filter">
              <span className="muted small">Status</span>
              <select
                value={statusFilter}
                onChange={(e) =>
                  setStatusFilter(
                    e.target.value as ComparisonStatus | "all"
                  )
                }
              >
                <option value="all">Todos</option>
                {ALL_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABEL[s]}
                  </option>
                ))}
              </select>
            </label>

            <label className="dc-filter">
              <span className="muted small">Tipo</span>
              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="all">Todos</option>
                {types.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>

            {blocks.length > 1 && (
              <label className="dc-filter">
                <span className="muted small">Bloco</span>
                <select
                  value={blockFilter}
                  onChange={(e) => setBlockFilter(e.target.value)}
                >
                  <option value="all">Todos</option>
                  {blocks.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <label className="dc-filter dc-filter-search">
              <span className="muted small">Busca</span>
              <input
                type="text"
                placeholder="Filtrar por trecho, tipo ou contexto"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          </div>

          {report.blocks && report.blocks.length > 0 && (
            <ComparisonTextView
              blocks={report.blocks}
              items={report.items}
              selectedIndex={selectedIndex}
              onSelect={handleHighlightClick}
            />
          )}

          <div className="dc-table-wrap">
            <table className="dc-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Trecho</th>
                  <th>Tipo OPF</th>
                  <th>Tipo Regex</th>
                  <th>Bloco</th>
                  <th>Conf. OPF</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.length === 0 && (
                  <tr>
                    <td colSpan={7} className="muted small">
                      Nenhum resultado para os filtros atuais.
                    </td>
                  </tr>
                )}
                {filteredItems.flatMap((it) => {
                  // Use the original index in `report.items` so the
                  // "Detalhes" toggle is stable across filter changes.
                  const idx = report.items.indexOf(it);
                  const isOpen = expanded.has(idx);
                  const isSelected = selectedIndex === idx;
                  const rows = [
                    <tr
                      key={`row-${idx}`}
                      ref={(el) => {
                        if (el) rowRefs.current.set(idx, el);
                        else rowRefs.current.delete(idx);
                      }}
                      className={isSelected ? "dc-row-selected" : undefined}
                    >
                      <td>
                        <span className={statusClass(it.status)}>
                          <span aria-hidden>{STATUS_ICON[it.status]}</span>{" "}
                          {STATUS_LABEL[it.status]}
                        </span>
                      </td>
                      <td className="mono small">
                        {previewOf(it) || <span className="muted">—</span>}
                      </td>
                      <td className="mono small">
                        {it.opf_span?.entity_type ?? (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="mono small">
                        {it.regex_span?.entity_type ?? (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="mono small">{it.block_id}</td>
                      <td>{formatConfidence(it.opf_span?.confidence)}</td>
                      <td>
                        <button
                          className="btn btn-small"
                          onClick={() => toggle(idx)}
                        >
                          {isOpen ? "Recolher" : "Detalhes"}
                        </button>
                      </td>
                    </tr>,
                  ];
                  if (isOpen) {
                    rows.push(<DetailRow key={`detail-${idx}`} item={it} />);
                  }
                  return rows;
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
