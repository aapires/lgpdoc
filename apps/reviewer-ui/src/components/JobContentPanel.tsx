"use client";

import { useEffect, useRef, useState } from "react";

import { applyManualRedaction } from "@/lib/api";
import type { AppliedSpan, Job, Report } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers (mirror the review page)
// ---------------------------------------------------------------------------

const MANUAL_ENTITY_TYPES: Array<{ value: string; label: string }> = [
  { value: "private_person", label: "👤 Pessoa" },
  { value: "private_company", label: "🏛 Empresa/Órgão" },
  { value: "private_email", label: "📧 E-mail" },
  { value: "private_phone", label: "📞 Telefone" },
  { value: "private_address", label: "📍 Endereço" },
  { value: "cep", label: "📮 CEP" },
  { value: "private_date", label: "📅 Data" },
  { value: "cpf", label: "🪪 CPF" },
  { value: "cnpj", label: "🏢 CNPJ" },
  { value: "rg", label: "🆔 RG" },
  { value: "cnh", label: "🚗 CNH" },
  { value: "passaporte", label: "🛂 Passaporte" },
  { value: "titulo_eleitor", label: "🗳️ Título Eleitor" },
  { value: "pis", label: "📋 PIS / NIS" },
  { value: "ctps", label: "📋 CTPS" },
  { value: "sus", label: "🏥 Cartão SUS" },
  { value: "oab", label: "⚖️ OAB" },
  { value: "crm", label: "🩺 CRM" },
  { value: "crea", label: "🔧 CREA" },
  { value: "placa", label: "🚘 Placa" },
  { value: "renavam", label: "🚘 RENAVAM" },
  { value: "processo_cnj", label: "⚖️ Processo CNJ" },
  { value: "inscricao_estadual", label: "📋 Inscrição Estadual" },
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

function computeOffsets(
  spans: AppliedSpan[]
): Array<{ rstart: number; rend: number }> {
  const allAuth = spans.every(
    (s) =>
      typeof s.redacted_start === "number" &&
      typeof s.redacted_end === "number"
  );
  if (allAuth) {
    return spans.map((s) => ({
      rstart: s.redacted_start as number,
      rend: s.redacted_end as number,
    }));
  }
  const sorted = spans
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => s.doc_start >= 0)
    .sort((a, b) => a.s.doc_start - b.s.doc_start);
  let delta = 0;
  const result: Array<{ rstart: number; rend: number }> = new Array(spans.length);
  for (const { s, i } of sorted) {
    const rstart = s.doc_start + delta;
    result[i] = { rstart, rend: rstart + s.replacement.length };
    delta += s.replacement.length - (s.doc_end - s.doc_start);
  }
  for (let i = 0; i < spans.length; i++) {
    if (!result[i]) {
      const s = spans[i];
      result[i] = { rstart: s.local_start, rend: s.local_end };
    }
  }
  return result;
}

function buildHighlighted(
  text: string,
  offsets: Array<{ rstart: number; rend: number }>,
  spans: AppliedSpan[]
): React.ReactNode[] {
  const ranges = offsets
    .map((r, i) => ({ ...r, i }))
    .sort((a, b) => a.rstart - b.rstart);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const r of ranges) {
    if (r.rstart > cursor) parts.push(text.slice(cursor, r.rstart));
    const isFP = spans[r.i]?.false_positive === true;
    parts.push(
      <mark
        key={r.i}
        className={isFP ? "false-positive" : "handled"}
        title={spans[r.i]?.entity_type}
      >
        {text.slice(r.rstart, r.rend)}
      </mark>
    );
    cursor = r.rend;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

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

function ManualPopover({
  selection,
  busy,
  onConfirm,
  onCancel,
}: {
  selection: SelectionInfo;
  busy: boolean;
  onConfirm: (entityType: string) => void;
  onCancel: () => void;
}) {
  const [entityType, setEntityType] = useState(MANUAL_ENTITY_TYPES[0].value);
  const top = Math.min(selection.rect.bottom + 8, window.innerHeight - 200);
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
        <button
          className="btn btn-small"
          disabled={busy}
          onClick={onCancel}
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  job: Job;
  report: Report;
  onUpdated: (report: Report) => void;
}

export function JobContentPanel({ job, report, onUpdated }: Props) {
  const [selection, setSelection] = useState<SelectionInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);

  // Dismiss popover on outside click
  useEffect(() => {
    if (!selection) return;
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Node | null;
      if (!target) return;
      const popover = document.querySelector(".manual-popover");
      if (popover && popover.contains(target)) return;
      if (textRef.current && textRef.current.contains(target)) return;
      clear();
    }
    const id = setTimeout(
      () => document.addEventListener("mousedown", onMouseDown),
      0
    );
    return () => {
      clearTimeout(id);
      document.removeEventListener("mousedown", onMouseDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  }

  function clear() {
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  }

  async function onConfirm(entityType: string) {
    if (!selection) return;
    setBusy(true);
    try {
      const updated = await applyManualRedaction(
        job.job_id,
        selection.start,
        selection.end,
        entityType,
        selection.text
      );
      onUpdated(updated);
      const count =
        (updated as Report & { manual_redaction_occurrences?: number })
          .manual_redaction_occurrences ?? 1;
      flash(
        count === 1
          ? "Trecho anonimizado"
          : `${count} ocorrências anonimizadas`
      );
      clear();
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const text = report.redacted_text ?? "";
  const spans = report.applied_spans ?? [];
  const offsets = computeOffsets(spans);
  const totalSpans = spans.filter((s) => !s.false_positive).length;

  return (
    <div className="card">
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "baseline" }}
      >
        <h2 style={{ margin: 0 }}>📄 Conteúdo do documento</h2>
        <span className="muted small">
          {totalSpans === 0
            ? "nenhum trecho detectado automaticamente"
            : `${totalSpans} trecho(s) destacado(s)`}
        </span>
      </div>
      <p className="muted small" style={{ marginTop: 4, marginBottom: 10 }}>
        {totalSpans === 0 ? (
          <>
            O sistema não encontrou dados sensíveis. Confira o texto abaixo
            e <strong>selecione com o mouse</strong> qualquer trecho que
            tenha passado batido para anonimizar manualmente.
          </>
        ) : (
          <>
            <strong>Selecione qualquer texto</strong> abaixo para anonimizar
            manualmente o que o sistema deixou passar. Para revisar trecho
            a trecho (aceitar, editar, marcar falsos positivos), use{" "}
            <strong>Abrir revisão</strong> no topo.
          </>
        )}
      </p>

      <div
        ref={textRef}
        className="redacted-text"
        onMouseUp={() => {
          const sel = captureSelection(textRef.current);
          if (sel) setSelection(sel);
        }}
        style={{ maxHeight: 480 }}
      >
        {text ? buildHighlighted(text, offsets, spans) : (
          <span className="muted">(documento vazio)</span>
        )}
      </div>

      {selection && (
        <ManualPopover
          selection={selection}
          busy={busy}
          onConfirm={onConfirm}
          onCancel={clear}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
