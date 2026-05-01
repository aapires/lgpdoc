"use client";

import { useEffect, useState } from "react";

import {
  buildReversiblePackage,
  getReversibleStatus,
  restoreProcessedText,
  reversibleDownloadUrl,
  validateProcessedText,
} from "@/lib/api";
import type {
  Job,
  ReversiblePackage,
  ReversibleStatus,
  ValidationReport,
} from "@/lib/types";

export function ReversiblePanel({ job }: { job: Job }) {
  const [status, setStatus] = useState<ReversibleStatus | null>(null);
  const [pkg, setPkg] = useState<ReversiblePackage | null>(null);
  const [processedText, setProcessedText] = useState<string>("");
  const [validation, setValidation] = useState<ValidationReport | null>(null);
  const [restored, setRestored] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const s = await getReversibleStatus(job.job_id);
        if (cancelled) return;
        setStatus(s);
        // Auto-load the package on mount so the text is always visible —
        // even when zero PII was detected, the reviewer needs to see the
        // content to spot anything missed.
        if (s.available) {
          const p = await buildReversiblePackage(job.job_id);
          if (cancelled) return;
          setPkg(p);
        }
      } catch {
        /* status fetch is non-fatal */
      }
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [job.job_id]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  }

  async function loadPackage() {
    setBusy("package");
    try {
      const p = await buildReversiblePackage(job.job_id);
      setPkg(p);
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function copyText(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      flash(`${label} copiado para a área de transferência`);
    } catch (e) {
      flash(`Falha ao copiar: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function onValidate() {
    if (!processedText.trim()) {
      flash("Cole o texto processado primeiro");
      return;
    }
    setBusy("validate");
    try {
      const v = await validateProcessedText(job.job_id, processedText);
      setValidation(v);
      flash(
        v.valid
          ? "✓ Marcadores íntegros — pode restaurar"
          : "⚠ Há problemas com os marcadores — veja abaixo"
      );
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function onRestore() {
    if (!processedText.trim()) {
      flash("Cole o texto processado primeiro");
      return;
    }
    setBusy("restore");
    try {
      const r = await restoreProcessedText(job.job_id, processedText);
      setRestored(r.restored_text);
      setValidation(r.validation);
      // Refresh status to flip has_restored
      const s = await getReversibleStatus(job.job_id);
      setStatus(s);
      flash("Dados originais restaurados");
    } catch (e) {
      flash(`Falha: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  if (!status) {
    return (
      <div className="card">
        <p className="muted">Carregando informações de pseudonimização…</p>
      </div>
    );
  }

  if (!status.available) {
    return null; // job not in reversible mode or artefacts missing
  }

  const canDownload =
    status.has_restored &&
    (job.status === "approved" || job.status === "auto_approved");

  return (
    <div className="card reversible-panel">
      <h2>🔄 Pseudonimização reversível</h2>
      <p className="muted small" style={{ marginTop: -4 }}>
        Este documento foi processado com marcadores estáveis (
        <strong>{status.placeholder_count}</strong> únicos). Você pode usar o
        texto pseudonimizado externamente e depois restaurar os dados
        originais.
      </p>

      {/* Step 1 — package */}
      <div className="reversible-step">
        <div className="reversible-step-title">
          1. Texto pseudonimizado
        </div>
        {status.placeholder_count === 0 && pkg && (
          <p className="muted small" style={{ marginTop: -4, marginBottom: 8 }}>
            Nenhum dado sensível foi detectado automaticamente. Confira o
            texto abaixo e, se notar algo que passou batido, abra a tela
            de revisão para selecionar e anonimizar manualmente.
          </p>
        )}
        {!pkg ? (
          <button
            className="btn btn-primary"
            onClick={loadPackage}
            disabled={busy === "package"}
          >
            {busy === "package" ? "Carregando…" : "Carregar texto"}
          </button>
        ) : (
          <>
            <textarea
              readOnly
              value={pkg.pseudonymized_text}
              style={{ minHeight: 120, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
            />
            <div className="btn-row" style={{ marginTop: 6 }}>
              <button
                className="btn btn-small btn-primary"
                onClick={() =>
                  copyText(pkg.pseudonymized_text, "Texto pseudonimizado")
                }
              >
                📋 Copiar texto pseudonimizado
              </button>
              <button
                className="btn btn-small"
                onClick={() => copyText(pkg.instructions, "Instruções")}
              >
                📋 Copiar instruções
              </button>
            </div>
            <details style={{ marginTop: 8 }}>
              <summary
                style={{ cursor: "pointer", fontSize: 12, color: "var(--muted)" }}
              >
                Ver instruções para o sistema externo
              </summary>
              <pre
                style={{
                  fontSize: 11,
                  background: "var(--bg)",
                  padding: 8,
                  borderRadius: 4,
                  marginTop: 4,
                  whiteSpace: "pre-wrap",
                }}
              >
                {pkg.instructions}
              </pre>
            </details>
          </>
        )}
      </div>

      {/* Step 2 — paste processed */}
      <div className="reversible-step">
        <div className="reversible-step-title">
          2. Colar texto processado externamente
        </div>
        <textarea
          placeholder="Cole aqui o texto retornado pelo sistema externo (com os marcadores intactos)"
          value={processedText}
          onChange={(e) => setProcessedText(e.target.value)}
          style={{ minHeight: 120, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
        />
        <div className="btn-row" style={{ marginTop: 6 }}>
          <button
            className="btn btn-small"
            onClick={onValidate}
            disabled={busy !== null || !processedText.trim()}
          >
            {busy === "validate" ? "Validando…" : "🔍 Validar marcadores"}
          </button>
          <button
            className="btn btn-small btn-primary"
            onClick={onRestore}
            disabled={busy !== null || !processedText.trim()}
          >
            {busy === "restore" ? "Restaurando…" : "↩ Restaurar dados originais"}
          </button>
        </div>
      </div>

      {/* Validation feedback */}
      {validation && (
        <div
          className={`reversible-validation ${
            validation.valid ? "ok" : "warn"
          }`}
        >
          {validation.valid ? (
            <p>
              <strong>✓ Marcadores íntegros.</strong> Cada marcador aparece o
              número esperado de vezes e nenhum estranho foi adicionado.
            </p>
          ) : (
            <>
              <p>
                <strong>⚠ Problemas encontrados nos marcadores:</strong>
              </p>
              {validation.missing.length > 0 && (
                <div>
                  <strong>Faltando:</strong>
                  <ul>
                    {validation.missing.map((m) => (
                      <li key={m.placeholder}>
                        <code>{m.placeholder}</code> — esperado {m.expected},
                        encontrado {m.actual}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {validation.duplicated.length > 0 && (
                <div>
                  <strong>Duplicados:</strong>
                  <ul>
                    {validation.duplicated.map((d) => (
                      <li key={d.placeholder}>
                        <code>{d.placeholder}</code> — esperado {d.expected},
                        encontrado {d.actual}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {validation.unexpected.length > 0 && (
                <div>
                  <strong>Inesperados (não vieram do original):</strong>
                  <ul>
                    {validation.unexpected.map((u) => (
                      <li key={u}>
                        <code>{u}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Step 3 — restored output */}
      {restored !== null && (
        <div className="reversible-step">
          <div className="reversible-step-title">3. Texto restaurado</div>
          <textarea
            readOnly
            value={restored}
            style={{ minHeight: 120, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
          />
          <div className="btn-row" style={{ marginTop: 6 }}>
            <button
              className="btn btn-small"
              onClick={() => copyText(restored, "Texto restaurado")}
            >
              📋 Copiar texto restaurado
            </button>
            {canDownload ? (
              <a
                className="btn btn-small btn-primary"
                href={reversibleDownloadUrl(job.job_id)}
              >
                ⬇ Baixar texto restaurado
              </a>
            ) : (
              <span className="muted small" style={{ alignSelf: "center" }}>
                Aprove o documento para liberar o download
              </span>
            )}
          </div>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
