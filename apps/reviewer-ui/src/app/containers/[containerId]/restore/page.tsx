"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  getContainer,
  listContainerDocuments,
  restoreContainerDocument,
  restoreContainerText,
} from "@/lib/api";
import type {
  Container,
  ContainerDocument,
  ContainerRestoreResult,
} from "@/lib/types";

export default function ContainerRestorePage({
  params,
}: {
  params: { containerId: string };
}) {
  const [container, setContainer] = useState<Container | null>(null);
  const [documents, setDocuments] = useState<ContainerDocument[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [pastedText, setPastedText] = useState("");
  const [busy, setBusy] = useState<"text" | "document" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<ContainerRestoreResult | null>(null);

  const [selectedDocumentId, setSelectedDocumentId] = useState<string>("");

  useEffect(() => {
    let alive = true;
    Promise.all([
      getContainer(params.containerId),
      listContainerDocuments(params.containerId),
    ])
      .then(([c, ds]) => {
        if (!alive) return;
        setContainer(c);
        setDocuments(ds);
        const ready = ds.find((d) => d.status === "ready");
        if (ready) setSelectedDocumentId(ready.document_id);
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [params.containerId]);

  async function handleRestoreText() {
    setBusy("text");
    setActionError(null);
    try {
      const r = await restoreContainerText(params.containerId, pastedText);
      setResult(r);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleRestoreDocument() {
    if (!selectedDocumentId) return;
    setBusy("document");
    setActionError(null);
    try {
      const r = await restoreContainerDocument(
        params.containerId,
        selectedDocumentId
      );
      setResult(r);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function handleCopy() {
    if (!result) return;
    navigator.clipboard.writeText(result.restored_text).catch(() => {
      window.alert("Não foi possível copiar para a área de transferência.");
    });
  }

  if (error) return <p className="muted">Falha: {error}</p>;
  if (!container) return <p className="muted">Carregando…</p>;

  const restorableDocuments = documents.filter((d) => d.status === "ready");

  return (
    <div>
      <p style={{ marginBottom: 12 }}>
        <Link
          href={`/containers/${params.containerId}`}
          className="muted"
        >
          ← voltar para o container
        </Link>
      </p>

      <div className="page-title">
        <div>
          <h1>Restaurar dados originais</h1>
          <p className="page-subtitle">
            Container <strong>{container.name}</strong>. Substitui
            marcadores como <code>[PESSOA_0001]</code> pelo valor real
            registrado <em>neste container</em>. Marcadores que não
            existem aqui são reportados como desconhecidos e não são
            tocados.
          </p>
        </div>
      </div>

      <div className="card mapping-warning">
        <p style={{ margin: 0 }}>
          ⚠ O texto restaurado contém dados pessoais sensíveis. Trate-o
          como cópia da tabela de conversão: não compartilhe sem
          necessidade e mantenha apenas em ambiente seguro.
        </p>
      </div>

      <div className="restore-grid">
        {/* Texto colado */}
        <div className="card">
          <h2>Texto colado</h2>
          <p className="muted small">
            Cole aqui um texto pseudonimizado (por exemplo, a saída de um
            processo externo) para restaurar os dados originais usando a
            tabela de conversão deste container.
          </p>
          <textarea
            value={pastedText}
            onChange={(e) => setPastedText(e.target.value)}
            placeholder="Cole aqui o texto contendo marcadores como [PESSOA_0001]..."
            rows={8}
            disabled={busy !== null}
          />
          <div className="form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleRestoreText}
              disabled={busy !== null || !pastedText.trim()}
            >
              {busy === "text"
                ? "Restaurando…"
                : "Restaurar dados originais"}
            </button>
          </div>
        </div>

        {/* Documento processado */}
        <div className="card">
          <h2>Documento do container</h2>
          <p className="muted small">
            Restaurar o texto pseudonimizado de um documento já
            processado neste container.
          </p>
          {restorableDocuments.length === 0 ? (
            <p className="muted">
              Nenhum documento <code>ready</code> neste container ainda.
            </p>
          ) : (
            <>
              <label className="form-field">
                <span className="form-label">Documento</span>
                <select
                  value={selectedDocumentId}
                  onChange={(e) => setSelectedDocumentId(e.target.value)}
                  disabled={busy !== null}
                >
                  {restorableDocuments.map((d) => (
                    <option key={d.document_id} value={d.document_id}>
                      {d.filename}
                    </option>
                  ))}
                </select>
              </label>
              <div className="form-actions" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleRestoreDocument}
                  disabled={busy !== null || !selectedDocumentId}
                >
                  {busy === "document"
                    ? "Restaurando…"
                    : "Restaurar dados originais"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {actionError && (
        <div
          className="card"
          style={{
            borderColor: "var(--red)",
            color: "var(--red)",
            background: "var(--red-bg)",
          }}
        >
          ❌ {actionError}
        </div>
      )}

      {result && (
        <div className="card">
          <div
            className="row"
            style={{
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <h2 style={{ margin: 0 }}>Resultado</h2>
            <button
              type="button"
              className="btn btn-small"
              onClick={handleCopy}
            >
              📋 Copiar texto restaurado
            </button>
          </div>

          <div
            className="row"
            style={{ gap: 16, flexWrap: "wrap", margin: "10px 0" }}
          >
            <span className="small">
              <strong>{result.replaced_token_count}</strong> token(s)
              substituído(s)
            </span>
            <span className="small">
              <strong>{result.replaced_unique_count}</strong> marcador(es)
              único(s)
            </span>
          </div>

          {!result.is_clean && (
            <div
              style={{
                background: "var(--yellow-tint)",
                border: "1px solid var(--yellow-bg)",
                borderRadius: "var(--radius-sm)",
                padding: "10px 12px",
                marginBottom: 10,
                color: "var(--yellow)",
              }}
            >
              ⚠ Atenção:
              {result.unknown_markers.length > 0 && (
                <p style={{ margin: "4px 0 0 0" }}>
                  <strong>{result.unknown_markers.length}</strong> marcador(es)
                  desconhecido(s) — não existem na tabela de conversão
                  deste container e foram mantidos no texto:{" "}
                  <code className="small">
                    {result.unknown_markers.slice(0, 5).join(" ")}
                  </code>
                  {result.unknown_markers.length > 5 && " …"}
                </p>
              )}
              {result.malformed_markers.length > 0 && (
                <p style={{ margin: "4px 0 0 0" }}>
                  <strong>{result.malformed_markers.length}</strong>{" "}
                  marcador(es) com formato inválido:{" "}
                  <code className="small">
                    {result.malformed_markers.slice(0, 5).join(" ")}
                  </code>
                  {result.malformed_markers.length > 5 && " …"}
                </p>
              )}
            </div>
          )}

          <pre className="restore-output">{result.restored_text}</pre>
        </div>
      )}
    </div>
  );
}
