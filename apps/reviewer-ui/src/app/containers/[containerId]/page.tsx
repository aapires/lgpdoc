"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  containerBundleDownloadUrl,
  containerDocumentDownloadUrl,
  deleteContainer,
  deleteContainerDocument,
  getContainer,
  listContainerDocuments,
  uploadPseudonymizedContainerDocument,
  uploadRawContainerDocument,
} from "@/lib/api";
import type { Container, ContainerDocument } from "@/lib/types";

const ACCEPT = ".txt,.md,.rtf,.pdf,.docx,.xlsx,.xls,.png,.jpg,.jpeg";

const FORMAT_ICON: Record<string, string> = {
  pdf: "📕",
  docx: "📘",
  xlsx: "📊",
  xls: "📊",
  txt: "📄",
  md: "📝",
};

const STATUS_LABEL: Record<ContainerDocument["status"], string> = {
  pending: "pendente",
  processing: "processando",
  pending_review: "aguardando revisão",
  ready: "pronto",
  rejected: "rejeitado",
  failed: "falhou",
};

const SOURCE_LABEL: Record<ContainerDocument["source_type"], string> = {
  raw_sensitive_document: "documento sensível",
  already_pseudonymized_document: "já pseudonimizado",
};

// Tradução PT-BR dos valores de ``role`` que o backend persiste como
// strings livres (``source`` / ``analysis`` / ``summary`` / ``report``
// / ``edited_version`` / ``other``). Valores fora dessa lista caem
// pelo fallback (mostra o original).
const ROLE_LABEL: Record<string, string> = {
  source: "fonte",
  analysis: "análise",
  summary: "resumo",
  report: "laudo",
  edited_version: "versão editada",
  other: "outro",
};

function formatRole(role: string): string {
  return ROLE_LABEL[role] ?? role;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(s: string): string {
  return new Date(s).toLocaleString("pt-BR");
}

export default function ContainerDetailPage({
  params,
}: {
  params: { containerId: string };
}) {
  const router = useRouter();
  const rawInputRef = useRef<HTMLInputElement>(null);
  const pseudoInputRef = useRef<HTMLInputElement>(null);
  const [container, setContainer] = useState<Container | null>(null);
  const [documents, setDocuments] = useState<ContainerDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<
    "raw" | "pseudonymized" | null
  >(null);

  const refresh = useCallback(async () => {
    try {
      const [c, docs] = await Promise.all([
        getContainer(params.containerId),
        listContainerDocuments(params.containerId),
      ]);
      setContainer(c);
      setDocuments(docs);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [params.containerId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleUploadRaw(file: File) {
    setUploading("raw");
    setUploadError(null);
    try {
      await uploadRawContainerDocument(params.containerId, file);
      await refresh();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(null);
    }
  }

  async function handleUploadPseudonymized(file: File) {
    setUploading("pseudonymized");
    setUploadError(null);
    try {
      await uploadPseudonymizedContainerDocument(
        params.containerId,
        file
      );
      await refresh();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(null);
    }
  }

  async function handleDeleteDocument(doc: ContainerDocument) {
    const ok = window.confirm(
      `Apagar definitivamente "${doc.filename}"?\n\n` +
        "O texto pseudonimizado e os spans deste documento serão " +
        "removidos. A tabela de conversão do container permanece — " +
        "marcadores criados a partir deste documento ainda serão " +
        "reutilizáveis."
    );
    if (!ok) return;
    try {
      await deleteContainerDocument(params.containerId, doc.document_id);
      await refresh();
    } catch (e) {
      window.alert(
        `Falha ao apagar: ${e instanceof Error ? e.message : String(e)}`
      );
    }
  }

  async function handleDeleteContainer() {
    if (!container) return;
    const ok = window.confirm(
      `Apagar definitivamente o container "${container.name}"?\n\n` +
        "Esta ação remove o container e seus metadados. Em sprints " +
        "futuras, removerá também todos os documentos e a tabela de " +
        "conversão. Não pode ser desfeita."
    );
    if (!ok) return;
    setBusy(true);
    try {
      await deleteContainer(container.container_id);
      router.push("/containers");
    } catch (e) {
      window.alert(
        `Falha ao apagar: ${e instanceof Error ? e.message : String(e)}`
      );
      setBusy(false);
    }
  }

  if (error) return <p className="muted">Falha: {error}</p>;
  if (!container) return <p className="muted">Carregando…</p>;

  const readyCount = documents.filter((d) => d.status === "ready").length;

  return (
    <div>
      <p style={{ marginBottom: 12 }}>
        <Link href="/containers" className="muted">
          ← voltar para a lista
        </Link>
      </p>

      <div className="container-hero">
        <div className="container-hero-icon" aria-hidden>
          🗂️
        </div>
        <div className="container-hero-info">
          <h1 className="container-hero-name">{container.name}</h1>
          <div className="container-hero-id">{container.container_id}</div>
          {container.description && (
            <p className="container-hero-desc">{container.description}</p>
          )}
          <div className="container-hero-pills">
            <span
              className={`container-stat-status status-${container.status}`}
            >
              {container.status === "active" ? "ativo" : "arquivado"}
            </span>
            <span className="muted small">
              criado em {formatDate(container.created_at)}
            </span>
          </div>
        </div>
      </div>

      <div className="container-stats-grid">
        <div className="container-stat-card">
          <div className="container-stat-card-label">Documentos</div>
          <div className="container-stat-card-value">{documents.length}</div>
        </div>
        <div className="container-stat-card">
          <div className="container-stat-card-label">Marcadores</div>
          <div className="container-stat-card-value">
            {container.marker_count}
          </div>
        </div>
      </div>

      {/*
       * Ações organizadas em três zonas lógicas pra evitar confusão entre
       * o que opera com dados em claro (sensíveis) e o que opera com
       * texto pseudonimizado (com marcadores). A tabela de conversão é
       * a ponte entre os dois mundos e fica em um terceiro bloco.
       */}

      <div
        className="card"
        style={{
          borderLeft: "4px solid var(--orange)",
        }}
      >
        <h2>🔓 Operações com dados sensíveis</h2>
        <p className="muted small" style={{ marginBottom: 12 }}>
          Estas ações trabalham com <strong>conteúdo em claro</strong> —
          o documento original (na entrada) ou o texto restaurado (na
          saída) contém PII visível. Documentos novos passam pelo
          pipeline de pseudonimização do container e ganham marcadores
          como <code>[PESSOA_0001]</code>; o mesmo valor detectado em
          mais de um documento reutiliza o mesmo marcador.
        </p>
        <div className="btn-row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={uploading !== null}
            onClick={() => rawInputRef.current?.click()}
            title="Sobe um arquivo bruto com PII visível. O sistema detecta e propõe os marcadores; você revisa antes de aprovar."
          >
            {uploading === "raw"
              ? "Enviando…"
              : "📥 Adicionar documento sensível"}
          </button>
          <Link
            href={`/containers/${container.container_id}/restore`}
            className="btn"
            title="Reverte marcadores aos valores reais usando a tabela deste container."
          >
            🔄 Restaurar dados originais
          </Link>
        </div>
      </div>

      <div
        className="card"
        style={{
          borderLeft: "4px solid var(--green)",
        }}
      >
        <h2>🛡️ Operações com dados pseudonimizados</h2>
        <p className="muted small" style={{ marginBottom: 12 }}>
          Estas ações trabalham com <strong>texto já pseudonimizado</strong>
          — conteúdo onde a PII foi substituída por marcadores. Seguro
          para compartilhar com revisores externos, LLMs ou outros
          processos.
        </p>
        <div className="btn-row">
          <button
            type="button"
            className="btn"
            disabled={uploading !== null}
            onClick={() => pseudoInputRef.current?.click()}
            title="Importa um documento que já passou por pseudonimização. O sistema valida os marcadores contra a tabela do container."
          >
            {uploading === "pseudonymized"
              ? "Enviando…"
              : "📑 Adicionar documento já pseudonimizado"}
          </button>
          {readyCount > 0 ? (
            <a
              href={containerBundleDownloadUrl(container.container_id)}
              className="btn"
              title={`Baixa um .zip com ${readyCount} documento(s) pseudonimizado(s).`}
            >
              📦 Baixar pacote (.zip) — {readyCount} doc.
            </a>
          ) : (
            <button
              type="button"
              className="btn"
              disabled
              title="Aprove pelo menos um documento para liberar o download do pacote."
            >
              📦 Baixar pacote (.zip)
            </button>
          )}
        </div>
      </div>

      <div
        className="card"
        style={{
          borderLeft: "4px solid var(--accent)",
        }}
      >
        <h2>🔗 Tabela de conversão</h2>
        <p className="muted small" style={{ marginBottom: 12 }}>
          A ponte entre os dois mundos: liga cada marcador ao valor real
          correspondente <em>neste container</em>. Tratada como dado
          sensível — exporte só para ambiente seguro.
        </p>
        <div className="btn-row">
          <Link
            href={`/containers/${container.container_id}/mapping`}
            className="btn"
          >
            📋 Ver tabela
          </Link>
        </div>
      </div>

      {/* Inputs ocultos compartilhados pelos dois botões de upload. */}
      <input
        ref={rawInputRef}
        type="file"
        accept={ACCEPT}
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleUploadRaw(file);
          e.target.value = "";
        }}
      />
      <input
        ref={pseudoInputRef}
        type="file"
        accept={ACCEPT}
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleUploadPseudonymized(file);
          e.target.value = "";
        }}
      />

      {uploadError && (
        <div className="card" style={{ borderColor: "var(--red)" }}>
          <p
            style={{
              color: "var(--red)",
              margin: 0,
              fontSize: 13,
            }}
          >
            ❌ {uploadError}
          </p>
        </div>
      )}

      <div className="card">
        <h2>Documentos</h2>
        {documents.length === 0 ? (
          <p className="muted">
            Nenhum documento neste container ainda. Use{" "}
            <strong>Adicionar documento sensível</strong> acima para
            enviar o primeiro arquivo.
          </p>
        ) : (
          <table className="container-docs-table">
            <thead>
              <tr>
                <th>Arquivo</th>
                <th>Tipo</th>
                <th>Papel</th>
                <th>Status</th>
                <th>Tamanho</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {documents.map((d) => {
                // Two distinct review screens:
                //   * raw uploads (job_id set) → /jobs/{job_id}/review
                //   * pseudonymized imports (no job_id) → dedicated
                //     /containers/{cid}/documents/{did}/review-pseudonymized
                // Ready / rejected raw docs still link so the operator
                // can inspect the approved redaction; ready
                // pseudonymized docs are just downloadable.
                const isPseudo =
                  d.source_type === "already_pseudonymized_document";
                const reviewHref = isPseudo
                  ? `/containers/${params.containerId}/documents/${d.document_id}/review-pseudonymized`
                  : d.job_id
                    ? `/jobs/${d.job_id}/review`
                    : null;
                const linkable =
                  reviewHref !== null &&
                  (d.status === "pending_review" ||
                    (!isPseudo &&
                      (d.status === "ready" || d.status === "rejected")));
                const filenameNode =
                  linkable && reviewHref ? (
                    <Link href={reviewHref}>{d.filename}</Link>
                  ) : (
                    d.filename
                  );
                return (
                  <tr key={d.document_id}>
                    <td>
                      <span aria-hidden style={{ marginRight: 6 }}>
                        {FORMAT_ICON[d.file_format] ?? "📄"}
                      </span>
                      {filenameNode}
                    </td>
                    <td className="small">{SOURCE_LABEL[d.source_type]}</td>
                    <td className="small">{formatRole(d.role)}</td>
                    <td>
                      <span
                        className={`doc-status doc-status-${d.status}`}
                        title={d.error_message ?? undefined}
                      >
                        {STATUS_LABEL[d.status]}
                      </span>
                    </td>
                    <td className="small muted">
                      {formatBytes(d.file_size)}
                    </td>
                    <td>
                      {d.status === "pending_review" && reviewHref && (
                        <Link
                          href={reviewHref}
                          className="btn btn-small btn-primary"
                          style={{ marginRight: 6 }}
                        >
                          Revisar →
                        </Link>
                      )}
                      {d.status === "ready" && (
                        <a
                          href={containerDocumentDownloadUrl(
                            params.containerId,
                            d.document_id
                          )}
                          className="btn btn-small"
                          title="Baixar texto pseudonimizado"
                          style={{ marginRight: 6 }}
                        >
                          ⬇ Baixar
                        </a>
                      )}
                      <button
                        className="btn-icon"
                        title="Apagar"
                        aria-label={`Apagar ${d.filename}`}
                        onClick={() => handleDeleteDocument(d)}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <details className="collapsible">
        <summary style={{ color: "var(--red)" }}>
          ⚠ Zona de exclusão
        </summary>
        <div className="collapsible-body">
          <p className="muted small">
            Remove o container e seus metadados. Em sprints futuras, esta
            ação remove também os documentos e a tabela de conversão.
          </p>
          <button
            className="btn btn-danger"
            onClick={handleDeleteContainer}
            disabled={busy}
          >
            Excluir definitivamente
          </button>
        </div>
      </details>
    </div>
  );
}
