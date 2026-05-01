"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  containerMappingExportSensitiveUrl,
  getContainer,
  getContainerMapping,
} from "@/lib/api";
import type { Container, ContainerMappingEntry } from "@/lib/types";

export default function ContainerMappingPage({
  params,
}: {
  params: { containerId: string };
}) {
  const [container, setContainer] = useState<Container | null>(null);
  const [entries, setEntries] = useState<ContainerMappingEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");

  useEffect(() => {
    let alive = true;
    Promise.all([
      getContainer(params.containerId),
      getContainerMapping(params.containerId),
    ])
      .then(([c, ms]) => {
        if (!alive) return;
        setContainer(c);
        setEntries(ms);
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [params.containerId]);

  const types = useMemo(() => {
    if (!entries) return [];
    return Array.from(new Set(entries.map((e) => e.entity_type))).sort();
  }, [entries]);

  const filtered = useMemo(() => {
    if (!entries) return [];
    const q = search.trim().toLowerCase();
    return entries.filter((e) => {
      if (typeFilter !== "all" && e.entity_type !== typeFilter) return false;
      if (q) {
        const haystack = [e.marker, e.entity_type, e.normalized_value]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [entries, search, typeFilter]);

  if (error) return <p className="muted">Falha: {error}</p>;
  if (!container || entries === null)
    return <p className="muted">Carregando…</p>;

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
          <h1>Tabela de conversão</h1>
          <p className="page-subtitle">
            Container <strong>{container.name}</strong> ·{" "}
            {entries.length} marcador(es) registrado(s).
          </p>
        </div>
        <div className="btn-row">
          <button
            type="button"
            className="btn btn-danger"
            onClick={(e) => {
              const ok = window.confirm(
                "Esta planilha contém dados pessoais sensíveis e " +
                  "permite reidentificar os documentos pseudonimizados. " +
                  "Armazene-a apenas em ambiente seguro.\n\n" +
                  "Deseja continuar?"
              );
              if (!ok) {
                e.preventDefault();
                return;
              }
              window.location.href = containerMappingExportSensitiveUrl(
                params.containerId
              );
            }}
          >
            ⚠ Exportar tabela sensível
          </button>
        </div>
      </div>

      <div className="card mapping-warning">
        <p style={{ margin: 0 }}>
          ⚠ Esta tabela liga marcadores como <code>[PESSOA_0001]</code> ao
          valor real correspondente <em>neste container</em>. Trate-a como
          dado sensível: não compartilhe, não exporte sem necessidade e
          mantenha apenas em ambiente seguro.
        </p>
      </div>

      <div className="card">
        <div
          className="row"
          style={{ gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}
        >
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

          <label className="dc-filter dc-filter-search">
            <span className="muted small">Busca</span>
            <input
              type="text"
              placeholder="Marcador, tipo, valor normalizado…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>

        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="mapping-table">
          <thead>
            <tr>
              <th>Marcador</th>
              <th>Tipo</th>
              <th>Valor normalizado</th>
              <th>Ocorrências</th>
              <th>Revisão</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="muted small">
                  Nenhum marcador para os filtros atuais.
                </td>
              </tr>
            )}
            {filtered.map((e) => (
              <tr key={e.id}>
                <td className="mono">{e.marker}</td>
                <td className="small">{e.entity_type}</td>
                <td className="mono small">{e.normalized_value}</td>
                <td className="small">
                  {e.occurrences.length === 0 ? (
                    <span className="muted">—</span>
                  ) : (
                    <ul
                      style={{
                        margin: 0,
                        paddingLeft: 16,
                        listStyleType: "disc",
                      }}
                    >
                      {e.occurrences.map((o) => (
                        <li
                          key={o.document_id}
                          title={o.document_id}
                        >
                          {o.filename}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="small">{e.review_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
