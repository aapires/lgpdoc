"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { createContainer } from "@/lib/api";

export default function NewContainerPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Informe um nome para o container.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await createContainer({
        name: name.trim(),
        description: description.trim() || undefined,
      });
      router.push(`/containers/${created.container_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  return (
    <div>
      <p style={{ marginBottom: 12 }}>
        <Link href="/containers" className="muted">
          ← voltar para a lista
        </Link>
      </p>

      <div className="card" style={{ maxWidth: 640 }}>
        <h1>Criar container de pseudonimização</h1>
        <p className="muted small" style={{ marginBottom: 16 }}>
          Um container é uma área de trabalho segura que agrupa documentos
          de uma mesma análise ou caso. Todos os documentos dentro do
          container compartilham a mesma{" "}
          <strong>tabela de conversão</strong> de marcadores.
        </p>

        <form onSubmit={handleSubmit} className="form-stack">
          <label className="form-field">
            <span className="form-label">Nome</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              disabled={submitting}
              placeholder="Ex.: Análise Alfa"
              autoFocus
            />
          </label>

          <label className="form-field">
            <span className="form-label">
              Descrição <span className="muted small">(opcional)</span>
            </span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
              disabled={submitting}
              placeholder="Contexto, escopo ou notas — não inclua dados sensíveis aqui."
              rows={4}
            />
            <span className="form-hint muted small">
              Não cole documentos, nomes ou identificadores reais nesta
              descrição — ela aparece em listas e logs administrativos.
            </span>
          </label>

          {error && (
            <div
              style={{
                color: "var(--red)",
                background: "var(--red-bg)",
                border: "1px solid var(--red)",
                borderRadius: 6,
                padding: 10,
              }}
            >
              ❌ {error}
            </div>
          )}

          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting || !name.trim()}
            >
              {submitting ? "Criando…" : "Criar container"}
            </button>
            <Link href="/containers" className="btn">
              Cancelar
            </Link>
          </div>
        </form>
      </div>
    </div>
  );
}
