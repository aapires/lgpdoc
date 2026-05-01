"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { deleteContainer, listContainers } from "@/lib/api";
import type { Container } from "@/lib/types";

function timeAgo(s: string): string {
  const d = new Date(s).getTime();
  const diff = Date.now() - d;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "agora mesmo";
  const min = Math.floor(sec / 60);
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h}h`;
  const days = Math.floor(h / 24);
  if (days < 30) return `há ${days}d`;
  return new Date(s).toLocaleDateString("pt-BR");
}

export default function ContainersPage() {
  const [containers, setContainers] = useState<Container[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await listContainers();
      setContainers(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleDelete(c: Container) {
    const ok = window.confirm(
      `Apagar definitivamente o container "${c.name}"?\n\n` +
        "Esta ação remove o container e seus metadados. " +
        "Em sprints futuras, removerá também os documentos e a tabela " +
        "de conversão associados. Não pode ser desfeita."
    );
    if (!ok) return;
    try {
      await deleteContainer(c.container_id);
      refresh();
    } catch (e) {
      window.alert(
        `Falha ao apagar: ${e instanceof Error ? e.message : String(e)}`
      );
    }
  }

  return (
    <div>
      <div className="page-title">
        <div>
          <h1>Containers de pseudonimização</h1>
          <p className="page-subtitle">
            Área de trabalho segura: agrupe documentos relacionados a uma
            mesma análise para que compartilhem a mesma{" "}
            <strong>tabela de conversão</strong>. Marcadores como{" "}
            <code>[PESSOA_0001]</code> ou <code>[CPF_0001]</code> são
            consistentes <em>dentro</em> do container.
          </p>
        </div>
        <div>
          <Link href="/containers/new" className="btn btn-primary">
            + Criar container
          </Link>
        </div>
      </div>

      {error && (
        <div className="card" style={{ borderColor: "var(--red)" }}>
          <p style={{ color: "var(--red)", margin: 0 }}>
            ❌ Erro ao carregar a lista: {error}
          </p>
        </div>
      )}

      {containers === null && !error && (
        <p className="muted">Carregando…</p>
      )}

      {containers !== null && containers.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">🗂️</div>
          <h2>Nenhum container ainda</h2>
          <p>
            Crie um container para começar a agrupar documentos de uma
            análise ou caso. Cada container mantém sua própria tabela de
            conversão de marcadores.
          </p>
          <Link
            href="/containers/new"
            className="btn btn-primary"
            style={{ marginTop: 12 }}
          >
            + Criar container
          </Link>
        </div>
      )}

      {containers !== null && containers.length > 0 && (
        <>
          <div
            className="row"
            style={{ marginBottom: 12, justifyContent: "space-between" }}
          >
            <h2 style={{ margin: 0 }}>{containers.length} container(s)</h2>
          </div>
          <div className="container-grid">
            {containers.map((c) => (
              <div key={c.container_id} className="container-card">
                <div className="container-card-header">
                  <span className="container-card-icon" aria-hidden>
                    🗂️
                  </span>
                  <div className="container-card-title">
                    <Link
                      href={`/containers/${c.container_id}`}
                      className="container-card-name"
                      style={{ color: "inherit" }}
                    >
                      {c.name}
                    </Link>
                    <div className="container-card-id">
                      {c.container_id.slice(0, 12)}… ·{" "}
                      atualizado {timeAgo(c.updated_at)}
                    </div>
                  </div>
                  <button
                    className="btn-icon"
                    title="Apagar definitivamente"
                    aria-label={`Apagar ${c.name}`}
                    onClick={() => handleDelete(c)}
                  >
                    ✕
                  </button>
                </div>

                {c.description && (
                  <p className="container-card-desc muted small">
                    {c.description}
                  </p>
                )}

                <div className="container-card-stats">
                  <div className="container-stat">
                    <div className="container-stat-value">
                      {c.document_count}
                    </div>
                    <div className="container-stat-label">documentos</div>
                  </div>
                  <div className="container-stat">
                    <div className="container-stat-value">
                      {c.marker_count}
                    </div>
                    <div className="container-stat-label">marcadores</div>
                  </div>
                  <div className="container-stat">
                    <div
                      className={`container-stat-status status-${c.status}`}
                    >
                      {c.status === "active" ? "ativo" : "arquivado"}
                    </div>
                  </div>
                </div>

                <div className="container-card-actions">
                  <Link
                    href={`/containers/${c.container_id}`}
                    className="btn btn-small btn-primary"
                  >
                    Abrir →
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
