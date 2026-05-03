"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  applyManualRedaction,
  approveJob,
  getJob,
  getReport,
  postReviewEvent,
  rejectJob,
  reprocessJob,
  revertSpan,
  unapproveJob,
} from "@/lib/api";
import { computeStats, sourceMeta } from "@/lib/sources";
import type {
  AppliedSpan,
  Job,
  Report,
  ReviewEventInput,
} from "@/lib/types";
import { OpfModeBadge } from "@/components/OpfModeBadge";
import { RiskBadge, StatusBadge } from "@/components/StatusBadge";

const CONTEXT_CHARS = 30;

type SpanStatus =
  | "pending"
  | "accepted"
  | "edited"
  | "false_positive";

interface SpanState {
  status: SpanStatus;
  replacement: string;
  comment: string;
}

const SPAN_STATUS_LABEL: Record<SpanStatus, string> = {
  pending: "pendente",
  accepted: "aceito",
  edited: "editado",
  false_positive: "falso positivo",
};

const ENTITY_TYPE_LABEL: Record<string, string> = {
  private_person: "Pessoa",
  private_email: "E-mail",
  private_phone: "Telefone",
  private_address: "Endereço",
  private_date: "Data",
  private_url: "URL",
  cpf: "CPF",
  cnpj: "CNPJ",
  rg: "RG",
  cnh: "CNH",
  passaporte: "Passaporte",
  titulo_eleitor: "Título Eleitor",
  pis: "PIS / NIS",
  ctps: "CTPS",
  sus: "Cartão SUS",
  oab: "OAB",
  crm: "CRM",
  crea: "CREA",
  placa: "Placa",
  renavam: "RENAVAM",
  processo_cnj: "Processo CNJ",
  inscricao_estadual: "Inscrição Estadual",
  ip: "Endereço IP",
  financeiro: "Financeiro",
  cep: "CEP",
  account_number: "Conta bancária",
  secret: "Segredo",
};

const STRATEGY_LABEL: Record<string, string> = {
  replace: "substituir",
  pseudonym: "pseudonimizar",
  mask: "mascarar",
  suppress: "suprimir",
  indexed: "indexar",
};

/**
 * Devolve os offsets de cada span no texto redigido atual.
 *
 * Quando o backend já forneceu posições autoritativas (campos
 * ``redacted_start`` / ``redacted_end``), elas são usadas diretamente.
 * Caso contrário (payload legado), recalculamos via delta math sobre
 * ``doc_start`` — só funciona se não houver spans manuais.
 */
function computeRedactedOffsets(
  spans: AppliedSpan[]
): Array<{ rstart: number; rend: number }> {
  const allHaveAuthoritative = spans.every(
    (s) =>
      typeof s.redacted_start === "number" && typeof s.redacted_end === "number"
  );
  if (allHaveAuthoritative) {
    return spans.map((s) => ({
      rstart: s.redacted_start as number,
      rend: s.redacted_end as number,
    }));
  }

  // Legacy fallback: delta math from doc_start. Manual spans have
  // doc_start = -1 and would corrupt this — but legacy data shouldn't have
  // manual spans either.
  const sorted = spans
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => s.doc_start >= 0)
    .sort((a, b) => a.s.doc_start - b.s.doc_start);
  let delta = 0;
  const result: Array<{ rstart: number; rend: number }> = new Array(
    spans.length
  );
  for (const { s, i } of sorted) {
    const rstart = s.doc_start + delta;
    const rend = rstart + s.replacement.length;
    result[i] = { rstart, rend };
    delta += s.replacement.length - (s.doc_end - s.doc_start);
  }
  // Spans not handled above (manual without authoritative): use local_*.
  for (let i = 0; i < spans.length; i++) {
    if (!result[i]) {
      const s = spans[i];
      result[i] = { rstart: s.local_start, rend: s.local_end };
    }
  }
  return result;
}

function buildHighlightedText(
  redactedText: string,
  redactedOffsets: Array<{ rstart: number; rend: number }>,
  spans: AppliedSpan[],
  activeIndex: number | null,
  states: SpanState[]
): React.ReactNode[] {
  const ranges = redactedOffsets
    .map((r, i) => ({ ...r, i }))
    .sort((a, b) => a.rstart - b.rstart);

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const r of ranges) {
    if (r.rstart > cursor) {
      parts.push(redactedText.slice(cursor, r.rstart));
    }
    const status = states[r.i]?.status;
    const isFalsePositive =
      status === "false_positive" || spans[r.i].false_positive === true;
    const isHandled = !!status && status !== "pending";
    const classes = [
      activeIndex === r.i ? "active" : "",
      isFalsePositive ? "false-positive" : isHandled ? "handled" : "",
    ]
      .filter(Boolean)
      .join(" ");
    parts.push(
      <mark
        key={r.i}
        className={classes || undefined}
        title={`#${r.i} ${
          ENTITY_TYPE_LABEL[spans[r.i].entity_type] ?? spans[r.i].entity_type
        }`}
        onClick={() => {
          const el = document.getElementById(`span-${r.i}`);
          el?.scrollIntoView({ behavior: "smooth", block: "center" });
        }}
      >
        {redactedText.slice(r.rstart, r.rend)}
      </mark>
    );
    cursor = r.rend;
  }
  if (cursor < redactedText.length) {
    parts.push(redactedText.slice(cursor));
  }
  return parts;
}

// Entity types available for manual redaction (matches the default policy).
const MANUAL_ENTITY_TYPES: Array<{ value: string; label: string }> = [
  // Personal data
  { value: "private_person", label: "👤 Pessoa" },
  { value: "private_company", label: "🏛 Empresa/Órgão" },
  { value: "private_email", label: "📧 E-mail" },
  { value: "private_phone", label: "📞 Telefone" },
  { value: "private_address", label: "📍 Endereço" },
  { value: "cep", label: "📮 CEP" },
  { value: "private_date", label: "📅 Data" },
  // Identity documents
  { value: "cpf", label: "🪪 CPF" },
  { value: "cnpj", label: "🏢 CNPJ" },
  { value: "rg", label: "🆔 RG" },
  { value: "cnh", label: "🚗 CNH" },
  { value: "passaporte", label: "🛂 Passaporte" },
  { value: "titulo_eleitor", label: "🗳️ Título Eleitor" },
  { value: "pis", label: "📋 PIS / NIS" },
  { value: "ctps", label: "📋 CTPS" },
  { value: "sus", label: "🏥 Cartão SUS" },
  // Professional registries
  { value: "oab", label: "⚖️ OAB" },
  { value: "crm", label: "🩺 CRM" },
  { value: "crea", label: "🔧 CREA" },
  // Vehicles
  { value: "placa", label: "🚘 Placa" },
  { value: "renavam", label: "🚘 RENAVAM" },
  // Legal / fiscal
  { value: "processo_cnj", label: "⚖️ Processo CNJ" },
  { value: "inscricao_estadual", label: "📋 Inscrição Estadual" },
  // Other
  { value: "ip", label: "🌐 Endereço IP" },
  { value: "financeiro", label: "💰 Financeiro" },
  { value: "account_number", label: "🏦 Conta bancária" },
  { value: "private_url", label: "🔗 URL" },
  { value: "secret", label: "🔑 Segredo" },
];

interface SelectionInfo {
  start: number;
  end: number;
  text: string;
  rect: { top: number; left: number; bottom: number; right: number };
}

/** Compute character offsets of the current selection within `container`. */
function captureSelection(container: HTMLElement | null): SelectionInfo | null {
  if (!container) return null;
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;

  const before = document.createRange();
  before.selectNodeContents(container);
  before.setEnd(range.startContainer, range.startOffset);
  const start = before.toString().length;
  const text = range.toString();
  if (!text.trim()) return null;
  const end = start + text.length;
  const rect = range.getBoundingClientRect();
  return {
    start,
    end,
    text,
    rect: {
      top: rect.top,
      left: rect.left,
      bottom: rect.bottom,
      right: rect.right,
    },
  };
}

function ManualRedactPopover({
  selection,
  onConfirm,
  onCancel,
  busy,
}: {
  selection: SelectionInfo;
  onConfirm: (entityType: string) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [entityType, setEntityType] = useState(MANUAL_ENTITY_TYPES[0].value);

  // Position the popover below the selection, clamped to the viewport.
  const top = Math.min(
    selection.rect.bottom + 8,
    window.innerHeight - 200
  );
  const left = Math.max(
    8,
    Math.min(selection.rect.left, window.innerWidth - 280)
  );

  const preview =
    selection.text.length > 80
      ? selection.text.slice(0, 80) + "…"
      : selection.text;

  return (
    <div
      className="manual-popover"
      style={{ top, left }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="popover-title">Anonimizar trecho selecionado</div>
      <div className="popover-fragment">{preview}</div>
      <select
        value={entityType}
        onChange={(e) => setEntityType(e.target.value)}
        disabled={busy}
      >
        {MANUAL_ENTITY_TYPES.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </select>
      <div className="btn-row">
        <button
          className="btn btn-small btn-primary"
          disabled={busy}
          onClick={() => onConfirm(entityType)}
        >
          Anonimizar
        </button>
        <button className="btn btn-small" disabled={busy} onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

export default function ReviewPage({
  params,
}: {
  params: { job_id: string };
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualSelection, setManualSelection] = useState<SelectionInfo | null>(
    null
  );
  const [manualBusy, setManualBusy] = useState<boolean>(false);
  const textRef = useRef<HTMLDivElement | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [spanStates, setSpanStates] = useState<SpanState[]>([]);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  useEffect(() => {
    getJob(params.job_id).then(setJob).catch((e) => setError(String(e)));
    getReport(params.job_id)
      .then((r) => {
        setReport(r);
        const spans = r.applied_spans ?? [];
        setSpanStates(
          spans.map((s) => ({
            status: s.false_positive ? "false_positive" : "pending",
            replacement: s.replacement,
            comment: "",
          }))
        );
      })
      .catch((e) => setError(String(e)));
  }, [params.job_id]);

  // Dismiss the manual-redaction popover when the user clicks outside it.
  useEffect(() => {
    if (!manualSelection) return;
    function onDocMouseDown(e: MouseEvent) {
      const target = e.target as Node | null;
      if (!target) return;
      const popover = document.querySelector(".manual-popover");
      if (popover && popover.contains(target)) return;
      // Allow clicking inside the redacted-text to start a new selection.
      if (textRef.current && textRef.current.contains(target)) return;
      clearManualSelection();
    }
    const id = setTimeout(
      () => document.addEventListener("mousedown", onDocMouseDown),
      0
    );
    return () => {
      clearTimeout(id);
      document.removeEventListener("mousedown", onDocMouseDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manualSelection]);

  const spans = useMemo(() => report?.applied_spans ?? [], [report]);
  const redactedOffsets = useMemo(() => computeRedactedOffsets(spans), [spans]);
  const detectionStats = useMemo(() => computeStats(spans), [spans]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  }

  function updateState(i: number, patch: Partial<SpanState>) {
    setSpanStates((prev) => {
      const next = [...prev];
      next[i] = { ...next[i], ...patch };
      return next;
    });
  }

  async function recordEvent(body: ReviewEventInput) {
    try {
      await postReviewEvent(params.job_id, body);
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
      throw e;
    }
  }

  async function onAccept(i: number) {
    const current = spanStates[i]?.status;
    if (current === "accepted") {
      // Toggle off: register an audit-trail comment and revert the
      // span's local state back to pending. No span-level "unaccept"
      // event type exists on the backend — a comment keeps the audit
      // log complete without inventing schema changes.
      await recordEvent({
        event_type: "comment",
        span_index: i,
        note: "Aceitação revertida",
      });
      updateState(i, { status: "pending" });
      flash(`Aceitação do trecho #${i} desfeita`);
      return;
    }
    await recordEvent({
      event_type: "accept",
      span_index: i,
    });
    updateState(i, { status: "accepted" });
    flash(`Trecho #${i} aceito`);
  }

  async function onSaveEdit(i: number) {
    const replacement = spanStates[i]?.replacement ?? "";
    await recordEvent({
      event_type: "edit",
      span_index: i,
      payload: { replacement },
    });
    updateState(i, { status: "edited" });
    flash(`Trecho #${i} editado`);
  }

  async function onFalsePositive(i: number) {
    try {
      const updated = await revertSpan(
        params.job_id,
        i,
        undefined,
        spanStates[i]?.comment || undefined
      );
      setReport(updated);
      updateState(i, { status: "false_positive" });
      flash(`Trecho #${i} marcado como falso positivo (original restaurado)`);
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function onComment(i: number) {
    const note = spanStates[i]?.comment;
    if (!note) return;
    await recordEvent({
      event_type: "comment",
      span_index: i,
      note,
    });
    flash(`Comentário salvo no trecho #${i}`);
  }

  function clearManualSelection() {
    setManualSelection(null);
    window.getSelection()?.removeAllRanges();
  }

  async function onConfirmManualRedaction(entityType: string) {
    if (!manualSelection) return;
    setManualBusy(true);
    try {
      const updated = await applyManualRedaction(
        params.job_id,
        manualSelection.start,
        manualSelection.end,
        entityType,
        manualSelection.text // expected_text — source of truth
      );
      // Refresh report and add states for any newly added spans.
      setReport(updated);
      const spans = updated.applied_spans ?? [];
      setSpanStates((prev) => {
        const next = [...prev];
        while (next.length < spans.length) {
          const s = spans[next.length];
          next.push({
            status: "pending",
            replacement: s.replacement,
            comment: "",
          });
        }
        return next;
      });
      const count = updated.manual_redaction_occurrences ?? 1;
      flash(
        count === 1
          ? "Trecho anonimizado"
          : `${count} ocorrências anonimizadas`
      );
      clearManualSelection();
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
      // Re-sync report so the user sees the actual server state.
      try {
        const fresh = await getReport(params.job_id);
        setReport(fresh);
      } catch {
        /* ignore */
      }
    } finally {
      setManualBusy(false);
    }
  }

  async function onToggleApprove() {
    if (!job) return;
    setBusy(true);
    try {
      if (job.status === "approved") {
        const updated = await unapproveJob(params.job_id);
        setJob(updated);
        flash("Aprovação desfeita");
      } else {
        const updated = await approveJob(params.job_id);
        setJob(updated);
        flash("Documento aceito");
      }
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function onReject() {
    setBusy(true);
    try {
      const updated = await rejectJob(params.job_id);
      setJob(updated);
      flash("Documento rejeitado");
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function onReprocess() {
    if (!job) return;
    const ok = window.confirm(
      "Vai apagar a revisão atual deste documento e refazer a " +
        "anonimização com as configurações ativas agora (presets, " +
        "detectores habilitados e estado do botão OPF).\n\n" +
        "Continuar?"
    );
    if (!ok) return;
    setBusy(true);
    flash("Reprocessando…");
    try {
      const started = await reprocessJob(params.job_id);
      setJob(started);
      // Poll until the worker leaves pending/processing, then reload the
      // full report so the UI reflects the new spans/decision.
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 600));
        const next = await getJob(params.job_id);
        if (next.status !== "pending" && next.status !== "processing") {
          setJob(next);
          break;
        }
        setJob(next);
      }
      const fresh = await getReport(params.job_id);
      setReport(fresh);
      flash("Documento reprocessado");
    } catch (e) {
      flash(`Falha ao reprocessar: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="muted">Falha: {error}</p>;
  if (!job || !report) return <p className="muted">Carregando…</p>;

  const text = report.redacted_text ?? "";
  const isAwaiting = job.status === "awaiting_review";
  const isApproved = job.status === "approved";
  const isCritical = job.risk_level === "critical";
  const isLowRisk = job.risk_level === "low";
  // The "Aceitar" button is a toggle: it appears whenever the reviewer can
  // act on the document (awaiting review or already approved). Clicking
  // when approved undoes the decision.
  const showApproveToggle = isAwaiting || isApproved;

  return (
    <div>
      <p style={{ display: "flex", gap: 16 }}>
        <Link href="/jobs">← voltar para a lista</Link>
        <Link href={`/jobs/${job.job_id}`}>detalhes do documento →</Link>
      </p>

      <div className="card">
        <div
          className="row"
          style={{
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <div>
            <h1 style={{ marginBottom: 4 }}>Revisar documento</h1>
            <p className="muted" style={{ margin: 0 }}>
              {job.source_filename} · <StatusBadge status={job.status} /> ·{" "}
              <RiskBadge level={job.risk_level} />{" "}
              {job.risk_score !== null && (
                <span>(pontuação {job.risk_score.toFixed(1)}) </span>
              )}
              <OpfModeBadge opfUsed={job.opf_used} />
            </p>
          </div>
          <button
            type="button"
            className="btn"
            onClick={onReprocess}
            disabled={busy}
            title={
              "Refaz a anonimização com as configurações ativas " +
              "(presets, detectores, botão OPF). Apaga a revisão atual."
            }
          >
            🔁 Reprocessar
          </button>
        </div>
        {isCritical && report.risk_assessment.reasons.length > 0 && (
          <details open style={{ marginTop: 12 }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              Motivos do alerta crítico
            </summary>
            <ul style={{ marginTop: 8 }}>
              {report.risk_assessment.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </details>
        )}
        {!isCritical && report.risk_assessment.reasons[0] && (
          <p className="muted" style={{ marginTop: 8 }}>
            {report.risk_assessment.reasons[0]}
          </p>
        )}

        {detectionStats.total > 0 && (
          <details className="detection-stats" style={{ marginTop: 14 }}>
            <summary className="detection-stats-summary">
              <strong>📊 {detectionStats.total} detecção(ões):</strong>{" "}
              <span className="muted small">
                {detectionStats.model > 0 && (
                  <>🤖 {detectionStats.model} modelo</>
                )}
                {detectionStats.model > 0 && detectionStats.regex > 0 && " · "}
                {detectionStats.regex > 0 && (
                  <>📋 {detectionStats.regex} regras</>
                )}
                {(detectionStats.model > 0 || detectionStats.regex > 0) &&
                  detectionStats.manual > 0 &&
                  " · "}
                {detectionStats.manual > 0 && (
                  <>✋ {detectionStats.manual} manuais</>
                )}
                {detectionStats.unknown > 0 && (
                  <> · ❓ {detectionStats.unknown} sem origem</>
                )}
              </span>
            </summary>
            <div className="detection-stats-body">
              <table>
                <thead>
                  <tr>
                    <th>Detector</th>
                    <th style={{ textAlign: "right", width: 80 }}>Trechos</th>
                  </tr>
                </thead>
                <tbody>
                  {detectionStats.bySource.map(({ source, count, meta }) => (
                    <tr key={source}>
                      <td>
                        <span aria-hidden style={{ marginRight: 6 }}>
                          {meta.emoji}
                        </span>
                        {meta.label}
                      </td>
                      <td style={{ textAlign: "right" }}>{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small" style={{ marginTop: 8 }}>
                Use esse breakdown para julgar o que cada detector está
                contribuindo. Se a maioria das detecções vier de regras
                determinísticas, o modelo pode estar sendo subutilizado —
                ou ao contrário, se o modelo dominar, as regras podem ser
                redundantes.
              </p>
            </div>
          </details>
        )}
      </div>

      <div className="review-grid">
        {/* Esquerda: texto redigido */}
        <div className="card">
          <h2>
            Texto redigido (
            {spans.length === 0 ? "nenhum trecho detectado" : `${spans.length} trechos`}
            )
          </h2>
          <p className="muted small" style={{ marginTop: -6 }}>
            {spans.length === 0 ? (
              <>
                O sistema não detectou nenhum dado sensível neste documento.
                Confira o texto abaixo e, se notar algo que passou batido,
                <strong> selecione com o mouse e anonimize manualmente</strong>.
              </>
            ) : (
              <>
                Trechos amarelos = pendentes · verdes = aceitos/editados ·{" "}
                laranja = falsos positivos (texto original restaurado).
                Clique em um trecho para localizá-lo na lista ao lado.{" "}
                <strong>Selecione qualquer texto</strong> para anonimizar
                manualmente o que o sistema deixou passar.
              </>
            )}
          </p>
          <div
            ref={textRef}
            className="redacted-text"
            onMouseUp={() => {
              const sel = captureSelection(textRef.current);
              if (sel) setManualSelection(sel);
            }}
          >
            {buildHighlightedText(
              text,
              redactedOffsets,
              spans,
              activeIndex,
              spanStates
            )}
          </div>
        </div>

        {/* Direita: ações + spans */}
        <div>
          <div className="card">
            <h2>Decisão</h2>

            {isAwaiting && isCritical && (
              <div
                style={{
                  marginBottom: 12,
                  padding: 10,
                  background: "var(--red-bg)",
                  borderRadius: 6,
                  border: "1px solid var(--red)",
                  fontSize: 12,
                }}
              >
                <strong style={{ color: "var(--red)" }}>
                  ⚠ Atenção redobrada
                </strong>
                <p style={{ margin: "4px 0 0 0" }}>
                  Este documento contém conteúdo de risco crítico (segredo,
                  JWT, chave privada ou similar). Confirme trecho a trecho
                  antes de aprovar. Veja os motivos no topo.
                </p>
              </div>
            )}

            {isAwaiting && isLowRisk && (
              <div
                style={{
                  marginBottom: 12,
                  padding: 10,
                  background: "var(--green-bg)",
                  borderRadius: 6,
                  border: "1px solid var(--green)",
                  fontSize: 12,
                }}
              >
                <strong style={{ color: "var(--green)" }}>
                  ✓ Risco baixo
                </strong>
                <p style={{ margin: "4px 0 0 0" }}>
                  O sistema considera este documento provavelmente seguro
                  (poucos ou nenhum dado sensível detectado). Mesmo assim,
                  confirme abaixo antes de liberar o download.
                </p>
              </div>
            )}

            {showApproveToggle ? (
              <div className="btn-row" style={{ marginTop: 12 }}>
                <button
                  className={
                    isApproved
                      ? "btn btn-success"
                      : "btn btn-primary"
                  }
                  onClick={onToggleApprove}
                  disabled={busy}
                  title={
                    isApproved
                      ? "Clique para desfazer a aprovação"
                      : "Aprova o documento e libera o download"
                  }
                >
                  {isApproved ? "✓ Aceito" : "Aceitar"}
                </button>
                {isAwaiting && (
                  <button
                    className="btn btn-danger"
                    onClick={onReject}
                    disabled={busy}
                  >
                    Rejeitar
                  </button>
                )}
                {isApproved && (
                  <span className="muted small">
                    Clique em <strong>Aceito</strong> para desfazer
                  </span>
                )}
              </div>
            ) : (
              <p className="muted" style={{ marginTop: 8 }}>
                Documento já em <code>{job.status}</code>. Aprovação/rejeição
                não estão mais disponíveis, mas você ainda pode{" "}
                <strong>selecionar texto e anonimizar manualmente</strong> —
                a redação atualiza o texto e os artefatos do job.
              </p>
            )}
          </div>

          <div className="card">
            <h2>Trechos detectados</h2>
            <div className="span-list">
              {spans.length === 0 && (
                <p className="muted">Nenhum trecho foi anonimizado.</p>
              )}
              {spans.map((s, i) => {
                const st = spanStates[i] ?? {
                  status: "pending" as SpanStatus,
                  replacement: s.replacement,
                  comment: "",
                };
                const entityLabel =
                  ENTITY_TYPE_LABEL[s.entity_type] ?? s.entity_type;
                const strategyLabel =
                  STRATEGY_LABEL[s.strategy] ?? s.strategy;

                // Prefer the original PII value (with surrounding context)
                // captured at detection time. Fall back to a slice of the
                // redacted text when the payload predates the new fields.
                const hasOriginal = typeof s.original_text === "string";
                let ctxBefore: string;
                let ctxMiddle: string;
                let ctxAfter: string;
                if (hasOriginal) {
                  ctxBefore = s.original_context_before ?? "";
                  ctxMiddle = s.original_text ?? "";
                  ctxAfter = s.original_context_after ?? "";
                } else {
                  const offsets = redactedOffsets[i];
                  const ctxStart = Math.max(0, offsets.rstart - CONTEXT_CHARS);
                  const ctxEnd = Math.min(
                    text.length,
                    offsets.rend + CONTEXT_CHARS
                  );
                  ctxBefore = text.slice(ctxStart, offsets.rstart);
                  ctxMiddle = text.slice(offsets.rstart, offsets.rend);
                  ctxAfter = text.slice(offsets.rend, ctxEnd);
                }
                return (
                  <div
                    key={i}
                    id={`span-${i}`}
                    className={`span-card ${
                      st.status !== "pending" ? "handled" : ""
                    }`}
                    onMouseEnter={() => setActiveIndex(i)}
                    onMouseLeave={() => setActiveIndex(null)}
                  >
                    <div className="span-meta">
                      #{i} · <strong>{entityLabel}</strong> · {strategyLabel}
                      {s.page !== null && <> · página {s.page}</>}
                      {st.status !== "pending" && (
                        <> · <em>{SPAN_STATUS_LABEL[st.status]}</em></>
                      )}
                    </div>
                    <div className="span-source" title={sourceMeta(s.source).label}>
                      <span aria-hidden>{sourceMeta(s.source).emoji}</span>{" "}
                      <span className="span-source-text">
                        Fonte: {sourceMeta(s.source).short}
                      </span>
                      {typeof s.confidence === "number" && (
                        <span className="muted small" style={{ marginLeft: 6 }}>
                          ({(s.confidence * 100).toFixed(0)}%)
                        </span>
                      )}
                    </div>
                    <div className="span-context-label">
                      {hasOriginal ? "Detectado" : "Contexto"}
                    </div>
                    <div className="span-context">
                      {ctxBefore}
                      <span className="span-pii">{ctxMiddle}</span>
                      {ctxAfter}
                    </div>

                    <div className="replacement-edit">
                      <label className="muted">Substituição</label>
                      <input
                        type="text"
                        value={st.replacement}
                        onChange={(e) =>
                          updateState(i, { replacement: e.target.value })
                        }
                      />
                    </div>

                    <input
                      type="text"
                      placeholder="comentário (opcional)"
                      value={st.comment}
                      onChange={(e) =>
                        updateState(i, { comment: e.target.value })
                      }
                      style={{ marginTop: 6 }}
                    />

                    <div className="span-actions btn-row">
                      <button
                        className={
                          st.status === "accepted"
                            ? "btn btn-small btn-success"
                            : "btn btn-small btn-primary"
                        }
                        onClick={() => onAccept(i)}
                        title={
                          st.status === "accepted"
                            ? "Clique para desfazer a aceitação deste trecho"
                            : "Marca este trecho como aceito"
                        }
                      >
                        {st.status === "accepted" ? "✓ Aceito" : "Aceitar"}
                      </button>
                      <button
                        className="btn btn-small"
                        onClick={() => onSaveEdit(i)}
                        disabled={st.replacement === s.replacement}
                      >
                        ✏ Salvar edição
                      </button>
                      <button
                        className="btn btn-small"
                        onClick={() => onFalsePositive(i)}
                      >
                        ⚠ Falso positivo
                      </button>
                      <button
                        className="btn btn-small"
                        onClick={() => onComment(i)}
                        disabled={!st.comment}
                      >
                        💬 Comentar
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {manualSelection && (
        <ManualRedactPopover
          selection={manualSelection}
          busy={manualBusy}
          onConfirm={onConfirmManualRedaction}
          onCancel={clearManualSelection}
        />
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
