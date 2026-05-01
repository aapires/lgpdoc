"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  applyPseudonymizedManualRedaction,
  approvePseudonymizedDocument,
  getPseudonymizedReview,
  rejectPseudonymizedDocument,
} from "@/lib/api";
import type {
  PseudonymizedReviewPayload,
  ResidualPiiSpan,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Entity type options for residual PII. Mirrored from the manual-redaction
// dropdown of /jobs/{id}/review so reviewers see consistent terminology
// across both flows.
// ---------------------------------------------------------------------------

const ENTITY_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
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
  { value: "oab", label: "⚖️ OAB" },
  { value: "processo_cnj", label: "⚖️ Processo CNJ" },
  { value: "financeiro", label: "💰 Financeiro" },
  { value: "private_url", label: "🔗 URL" },
  { value: "secret", label: "🔑 Segredo" },
  { value: "account_number", label: "🏦 Conta bancária" },
];

// Friendly entity type labels (PT-BR) for the cards.
const ENTITY_TYPE_LABEL: Record<string, string> = Object.fromEntries(
  ENTITY_TYPE_OPTIONS.map((o) => [
    o.value,
    o.label.replace(/^[^\s]+\s+/, ""), // drop the leading emoji
  ])
);

const STATUS_LABEL: Record<string, string> = {
  pending: "pendente",
  processing: "processando",
  pending_review: "aguardando revisão",
  ready: "pronto",
  rejected: "rejeitado",
  failed: "falhou",
};

// ---------------------------------------------------------------------------
// Highlight construction — same logic as before, just kept inline
// ---------------------------------------------------------------------------

type HighlightKind = "known" | "unknown" | "malformed" | "residual";

interface Highlight {
  start: number;
  end: number;
  kind: HighlightKind;
  label: string;
}

function buildHighlights(
  text: string,
  knownMarkers: string[],
  unknownMarkers: string[],
  malformedMarkers: string[],
  residual: ResidualPiiSpan[],
  dismissedResidualHashes: Set<string>
): Highlight[] {
  const highlights: Highlight[] = [];

  function pushAllOccurrences(
    tokens: string[],
    kind: "known" | "unknown" | "malformed"
  ) {
    for (const tok of tokens) {
      let cursor = 0;
      while (true) {
        const idx = text.indexOf(tok, cursor);
        if (idx === -1) break;
        highlights.push({
          start: idx,
          end: idx + tok.length,
          kind,
          label: tok,
        });
        cursor = idx + tok.length;
      }
    }
  }

  pushAllOccurrences(knownMarkers, "known");
  pushAllOccurrences(unknownMarkers, "unknown");
  pushAllOccurrences(malformedMarkers, "malformed");
  for (const r of residual) {
    if (dismissedResidualHashes.has(r.fragment_hash)) continue;
    highlights.push({
      start: r.start,
      end: r.end,
      kind: "residual",
      label: r.entity_type,
    });
  }

  highlights.sort((a, b) => a.start - b.start || a.end - b.end);
  const out: Highlight[] = [];
  let cursor = 0;
  for (const h of highlights) {
    if (h.start < cursor) continue;
    out.push(h);
    cursor = h.end;
  }
  return out;
}

function renderHighlightedText(
  text: string,
  highlights: Highlight[],
  activeKey: string | null
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  highlights.forEach((h, idx) => {
    if (h.start > cursor) {
      parts.push(<span key={`t-${cursor}`}>{text.slice(cursor, h.start)}</span>);
    }
    const key = `${h.kind}:${h.start}:${h.end}`;
    const isActive = activeKey === key;
    parts.push(
      <mark
        key={`h-${idx}`}
        className={
          `pp-mark pp-mark-${h.kind}` + (isActive ? " pp-mark-active" : "")
        }
        title={h.label}
      >
        {text.slice(h.start, h.end)}
      </mark>
    );
    cursor = h.end;
  });
  if (cursor < text.length) {
    parts.push(<span key={`t-${cursor}`}>{text.slice(cursor)}</span>);
  }
  return parts;
}

// ---------------------------------------------------------------------------
// Manual selection from the text view
// ---------------------------------------------------------------------------

interface ManualSelection {
  text: string;
}

function captureSelection(container: HTMLElement | null): ManualSelection | null {
  if (!container) return null;
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;
  const text = range.toString();
  if (!text.trim()) return null;
  return { text };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ReviewPseudonymizedPage({
  params,
}: {
  params: { containerId: string; documentId: string };
}) {
  const router = useRouter();
  const textRef = useRef<HTMLPreElement>(null);

  const [payload, setPayload] = useState<PseudonymizedReviewPayload | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<
    "approve" | "reject" | "redact" | null
  >(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  // Selection-to-anonymize (free-form, picks any text in the document)
  const [manualSelection, setManualSelection] = useState<ManualSelection | null>(
    null
  );
  const [manualEntityType, setManualEntityType] = useState<string>(
    ENTITY_TYPE_OPTIONS[0].value
  );

  // Per-residual choice of entity type (reviewer can override the
  // detector's suggestion before clicking Anonimizar). Keyed by
  // ``fragment_hash`` so the choice survives re-renders even if the
  // ResidualPiiSpan instance is recreated.
  const [residualEntityType, setResidualEntityType] = useState<
    Record<string, string>
  >({});

  // Residuals the reviewer dismissed as false positives (client-side
  // only — the backend has no notion of "residual false positive";
  // dismissing just hides the row and removes the highlight so the
  // reviewer can approve the document with the residual still in
  // place).
  const [dismissedResidual, setDismissedResidual] = useState<Set<string>>(
    new Set()
  );

  // Highlight that follows hover/focus in the side cards.
  const [activeHighlightKey, setActiveHighlightKey] = useState<string | null>(
    null
  );

  const refresh = async () => {
    try {
      const r = await getPseudonymizedReview(
        params.containerId,
        params.documentId
      );
      setPayload(r);
      setError(null);
      // Initialise per-residual entity type with detector's suggestion.
      const next: Record<string, string> = {};
      for (const sp of r.residual_pii) {
        next[sp.fragment_hash] = sp.entity_type;
      }
      setResidualEntityType(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.containerId, params.documentId]);

  const visibleResidual = useMemo(() => {
    if (!payload) return [];
    return payload.residual_pii.filter(
      (r) => !dismissedResidual.has(r.fragment_hash)
    );
  }, [payload, dismissedResidual]);

  const highlights = useMemo(() => {
    if (!payload) return [];
    return buildHighlights(
      payload.text,
      payload.validation.known_markers,
      payload.validation.unknown_markers,
      payload.validation.malformed_markers,
      payload.residual_pii,
      dismissedResidual
    );
  }, [payload, dismissedResidual]);

  function flash(msg: string) {
    setActionMsg(msg);
    setTimeout(() => setActionMsg(null), 3500);
  }

  async function handleApprove() {
    setBusy("approve");
    setActionMsg(null);
    try {
      await approvePseudonymizedDocument(
        params.containerId,
        params.documentId
      );
      router.push(`/containers/${params.containerId}`);
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(null);
    }
  }

  async function handleReject() {
    if (
      !window.confirm(
        "Rejeitar este documento? Ele ficará marcado como rejeitado " +
          "e não estará disponível para download."
      )
    ) {
      return;
    }
    setBusy("reject");
    setActionMsg(null);
    try {
      await rejectPseudonymizedDocument(
        params.containerId,
        params.documentId
      );
      router.push(`/containers/${params.containerId}`);
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(null);
    }
  }

  async function handleAnonymizeResidual(span: ResidualPiiSpan) {
    const entityType =
      residualEntityType[span.fragment_hash] ?? span.entity_type;
    setBusy("redact");
    try {
      const result = await applyPseudonymizedManualRedaction(
        params.containerId,
        params.documentId,
        span.fragment,
        entityType
      );
      flash(
        result.marker_created
          ? `Novo marcador ${result.marker} criado · ${result.occurrences} ocorrência(s) substituída(s).`
          : `Marcador ${result.marker} reutilizado · ${result.occurrences} ocorrência(s) substituída(s).`
      );
      await refresh();
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function handleManualRedaction() {
    if (!manualSelection) return;
    setBusy("redact");
    try {
      const result = await applyPseudonymizedManualRedaction(
        params.containerId,
        params.documentId,
        manualSelection.text,
        manualEntityType
      );
      flash(
        result.marker_created
          ? `Novo marcador ${result.marker} criado · ${result.occurrences} ocorrência(s) substituída(s).`
          : `Marcador ${result.marker} reutilizado · ${result.occurrences} ocorrência(s) substituída(s).`
      );
      setManualSelection(null);
      window.getSelection()?.removeAllRanges();
      await refresh();
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  function dismissResidual(span: ResidualPiiSpan) {
    setDismissedResidual((prev) => {
      const next = new Set(prev);
      next.add(span.fragment_hash);
      return next;
    });
  }

  if (error) return <p className="muted">Falha: {error}</p>;
  if (!payload) return <p className="muted">Carregando…</p>;

  const v = payload.validation;
  const hasIssues =
    v.unknown_markers.length > 0 ||
    v.malformed_markers.length > 0 ||
    visibleResidual.length > 0;
  const isPendingReview = payload.status === "pending_review";

  return (
    <div>
      <p>
        <Link href={`/containers/${params.containerId}`}>
          ← voltar para o container
        </Link>
      </p>

      <div className="card">
        <h1 style={{ marginBottom: 4 }}>Revisar documento pseudonimizado</h1>
        <p className="muted">
          {payload.filename} ·{" "}
          <span
            className={`doc-status doc-status-${payload.status}`}
            style={{ verticalAlign: "middle" }}
          >
            {STATUS_LABEL[payload.status] ?? payload.status}
          </span>
        </p>

        <details className="detection-stats" open style={{ marginTop: 14 }}>
          <summary className="detection-stats-summary">
            <strong>📊 Resumo da validação:</strong>{" "}
            <span className="muted small">
              ✓ {v.known_markers.length} conhecidos · ⚠{" "}
              {v.unknown_markers.length} desconhecidos · ⚡{" "}
              {v.malformed_markers.length} mal formados · 🌸{" "}
              {visibleResidual.length} dado(s) pessoal(is) detectado(s)
            </span>
          </summary>
          <div className="detection-stats-body">
            <p className="muted small" style={{ marginTop: 0 }}>
              Marcadores <strong>conhecidos</strong> já estão na tabela de
              conversão deste container.{" "}
              <strong>Desconhecidos</strong> são marcadores bem formados
              que não existem aqui (podem ser de outro container ou
              digitados à mão). <strong>Mal formados</strong> são tokens
              entre colchetes fora do padrão. <strong>Dados pessoais
              detectados</strong> são trechos que parecem PII mas estão
              sem marcador — confirme tipo e clique para anonimizar.
            </p>
          </div>
        </details>
      </div>

      {!hasIssues && (
        <div
          className="card"
          style={{
            background: "var(--green-bg)",
            borderColor: "var(--green)",
            color: "var(--green)",
          }}
        >
          ✅ Validação limpa: todos os marcadores são conhecidos e
          nenhum dado pessoal sem marcador foi detectado. Você ainda
          pode aprovar para mover o documento para o status <code>ready</code>.
        </div>
      )}

      <div className="review-grid">
        {/* Esquerda: texto pseudonimizado com highlights */}
        <div className="card">
          <h2>Texto pseudonimizado</h2>
          <p className="muted small" style={{ marginTop: -6 }}>
            🟢 verde = marcador conhecido · 🟡 amarelo = desconhecido ·
            🟠 laranja = mal formado · 🌸 rosa = dado pessoal detectado.{" "}
            <strong>Selecione qualquer trecho não marcado</strong> para
            anonimizar manualmente.
          </p>
          <pre
            ref={textRef}
            className="redacted-text"
            onMouseUp={() => {
              const sel = captureSelection(textRef.current);
              if (sel) setManualSelection(sel);
            }}
          >
            {renderHighlightedText(payload.text, highlights, activeHighlightKey)}
          </pre>

          {manualSelection && (
            <div
              className="card"
              style={{ marginTop: 12, background: "var(--surface-soft)" }}
            >
              <strong>Selecionado:</strong>{" "}
              <code className="mono small">{manualSelection.text}</code>
              <div
                className="row"
                style={{ marginTop: 8, gap: 8, alignItems: "center" }}
              >
                <select
                  value={manualEntityType}
                  onChange={(e) => setManualEntityType(e.target.value)}
                  disabled={busy !== null}
                >
                  {ENTITY_TYPE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <button
                  className="btn btn-primary btn-small"
                  onClick={handleManualRedaction}
                  disabled={busy !== null}
                >
                  Anonimizar trecho selecionado
                </button>
                <button
                  className="btn btn-small"
                  onClick={() => {
                    setManualSelection(null);
                    window.getSelection()?.removeAllRanges();
                  }}
                  disabled={busy !== null}
                >
                  Cancelar
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Direita: ações + lista de itens pra revisar */}
        <div>
          <div className="card">
            <h2>Decisão</h2>
            <p className="muted small" style={{ marginBottom: 8 }}>
              Aprove para mover o documento para o status{" "}
              <code>ready</code> (download liberado). Rejeitar marca o
              documento como rejeitado.
            </p>
            {isPendingReview ? (
              <div className="btn-row">
                <button
                  className="btn btn-primary"
                  onClick={handleApprove}
                  disabled={busy !== null}
                  title="Aprova o documento e libera o download"
                >
                  {busy === "approve" ? "Aprovando…" : "Aceitar"}
                </button>
                <button
                  className="btn btn-danger"
                  onClick={handleReject}
                  disabled={busy !== null}
                >
                  {busy === "reject" ? "Rejeitando…" : "Rejeitar"}
                </button>
              </div>
            ) : (
              <p className="muted">
                Documento já em <code>{payload.status}</code>. Aprovar /
                rejeitar não estão mais disponíveis.
              </p>
            )}
          </div>

          {/* Itens para revisar — espelha o "Trechos detectados" da
              revisão normal mas com cards específicos pra cada
              categoria. */}
          <div className="card">
            <h2>Itens para revisar</h2>
            {!hasIssues && (
              <p className="muted">
                Nenhum item exige sua atenção. Pode aprovar com
                segurança.
              </p>
            )}

            <div className="span-list">
              {/* Dados pessoais detectados — ação principal: escolher
                  tipo + anonimizar. */}
              {visibleResidual.map((sp) => {
                const key = `residual:${sp.start}:${sp.end}`;
                const chosenType =
                  residualEntityType[sp.fragment_hash] ?? sp.entity_type;
                return (
                  <div
                    key={sp.fragment_hash}
                    className="span-card"
                    onMouseEnter={() => setActiveHighlightKey(key)}
                    onMouseLeave={() => setActiveHighlightKey(null)}
                  >
                    <div className="span-meta">
                      <strong style={{ color: "#9d174d" }}>
                        🌸 Dado pessoal detectado
                      </strong>
                      {sp.detection_source && (
                        <> · fonte: <em>{sp.detection_source}</em></>
                      )}
                    </div>
                    <div className="span-context-label">Trecho</div>
                    <div className="span-context">
                      <span className="span-pii">{sp.fragment}</span>
                    </div>

                    <div
                      className="replacement-edit"
                      style={{ marginTop: 8 }}
                    >
                      <label className="muted small">
                        Tipo a anonimizar (sugestão pré-selecionada;
                        você pode alterar)
                      </label>
                      <select
                        value={chosenType}
                        onChange={(e) =>
                          setResidualEntityType((prev) => ({
                            ...prev,
                            [sp.fragment_hash]: e.target.value,
                          }))
                        }
                        disabled={busy !== null}
                        style={{ marginTop: 4 }}
                      >
                        {ENTITY_TYPE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="span-actions btn-row">
                      <button
                        className="btn btn-small btn-primary"
                        onClick={() => handleAnonymizeResidual(sp)}
                        disabled={busy !== null}
                        title={
                          `Cria (ou reutiliza) um marcador do tipo ` +
                          `${chosenType} e substitui no texto.`
                        }
                      >
                        Anonimizar como{" "}
                        <code className="small">
                          {ENTITY_TYPE_LABEL[chosenType] ?? chosenType}
                        </code>
                      </button>
                      <button
                        className="btn btn-small"
                        onClick={() => dismissResidual(sp)}
                        disabled={busy !== null}
                        title="Marca como falso positivo — o trecho fica como está e some desta lista."
                      >
                        ⚠ Falso positivo
                      </button>
                    </div>
                  </div>
                );
              })}

              {/* Marcadores desconhecidos — apenas informativos */}
              {v.unknown_markers.map((tok) => {
                const key = `unknown:${tok}`;
                return (
                  <div
                    key={`unk:${tok}`}
                    className="span-card"
                    onMouseEnter={() => {
                      // Highlight the FIRST occurrence in the text.
                      const idx = payload.text.indexOf(tok);
                      if (idx >= 0) {
                        setActiveHighlightKey(
                          `unknown:${idx}:${idx + tok.length}`
                        );
                      }
                    }}
                    onMouseLeave={() => setActiveHighlightKey(null)}
                  >
                    <div className="span-meta">
                      <strong style={{ color: "var(--yellow)" }}>
                        🟡 Marcador desconhecido
                      </strong>
                    </div>
                    <div className="span-context-label">Marcador</div>
                    <div className="span-context">
                      <span className="span-pii mono">{tok}</span>
                    </div>
                    <p className="muted small" style={{ marginTop: 6 }}>
                      Este marcador está bem formado mas não existe na
                      tabela de conversão deste container. Pode ter
                      vindo de outro container ou ter sido inserido
                      manualmente. Será mantido no texto como está.
                    </p>
                  </div>
                );
              })}

              {/* Marcadores mal formados */}
              {v.malformed_markers.map((tok) => (
                <div key={`mal:${tok}`} className="span-card">
                  <div className="span-meta">
                    <strong style={{ color: "var(--orange)" }}>
                      🟠 Marcador mal formado
                    </strong>
                  </div>
                  <div className="span-context-label">Token</div>
                  <div className="span-context">
                    <span className="span-pii mono">{tok}</span>
                  </div>
                  <p className="muted small" style={{ marginTop: 6 }}>
                    Token entre colchetes que não segue o padrão{" "}
                    <code>[LABEL_NNNN]</code>. Provável erro de
                    digitação durante o processamento externo. Será
                    mantido no texto como está.
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {actionMsg && <div className="toast">{actionMsg}</div>}
    </div>
  );
}
