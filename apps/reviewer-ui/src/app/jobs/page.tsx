"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { deleteJob, listJobs, recommendedAction } from "@/lib/api";
import type { Job } from "@/lib/types";
import { ModeBadge, RiskBadge, StatusBadge } from "@/components/StatusBadge";
import { UploadCard } from "@/components/UploadCard";

const POLL_MS = 1500;

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

function shortId(id: string): string {
  return id.split("-")[0];
}

function isInFlight(job: Job): boolean {
  return job.status === "pending" || job.status === "processing";
}

const FORMAT_ICON: Record<string, string> = {
  pdf: "📕",
  docx: "📘",
  xlsx: "📊",
  txt: "📄",
  md: "📝",
};

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const next = await listJobs();
      if (!mountedRef.current) return;
      setJobs(next);
      setError(null);
      if (next.some(isInFlight)) {
        timeoutRef.current = setTimeout(refresh, POLL_MS);
      }
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    return () => {
      mountedRef.current = false;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, [refresh]);

  async function handleDelete(job: Job) {
    const ok = window.confirm(
      `Apagar definitivamente "${job.source_filename}"?\n\n` +
        "Esta ação remove o documento original (quarentena), o texto " +
        "redigido, os spans detectados e todo o histórico de revisão. " +
        "Não pode ser desfeita."
    );
    if (!ok) return;
    try {
      await deleteJob(job.job_id);
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
          <h1>Documentos</h1>
          <p className="page-subtitle">
            Envie um arquivo, deixe o sistema identificar e tratar dados
            sensíveis, depois revise antes de exportar.
          </p>
        </div>
      </div>

      <UploadCard onUploaded={refresh} />

      {error && (
        <div className="card" style={{ borderColor: "var(--red)" }}>
          <p style={{ color: "var(--red)", margin: 0 }}>
            ❌ Erro ao carregar a lista: {error}
          </p>
          <p className="muted small" style={{ marginTop: 6 }}>
            Verifique se a API está rodando em{" "}
            <code>{process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:9000"}</code>.
          </p>
        </div>
      )}

      {jobs === null && !error && <p className="muted">Carregando…</p>}

      {jobs !== null && jobs.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">📂</div>
          <h2>Nenhum documento ainda</h2>
          <p>Use o formulário acima para enviar seu primeiro arquivo.</p>
          <div className="empty-steps">
            <div className="empty-step">
              <div className="empty-step-num">1</div>
              <div>
                <strong>Envie</strong> um arquivo (.txt .md .pdf .docx .xlsx)
              </div>
            </div>
            <div className="empty-step">
              <div className="empty-step-num">2</div>
              <div>
                O sistema <strong>identifica e redige</strong> automaticamente
                CPFs, e-mails, telefones, segredos e outros dados sensíveis
              </div>
            </div>
            <div className="empty-step">
              <div className="empty-step-num">3</div>
              <div>
                <strong>Revise</strong> trecho a trecho — aceite, edite ou
                marque falsos positivos
              </div>
            </div>
            <div className="empty-step">
              <div className="empty-step-num">4</div>
              <div>
                <strong>Aprove e baixe</strong> a versão tratada
              </div>
            </div>
          </div>
        </div>
      )}

      {jobs !== null && jobs.length > 0 && (
        <>
          <div
            className="row"
            style={{ marginBottom: 12, justifyContent: "space-between" }}
          >
            <h2 style={{ margin: 0 }}>{jobs.length} documento(s)</h2>
          </div>
          <div className="job-grid">
            {jobs.map((j) => {
              const action = recommendedAction(j);
              const inFlight = isInFlight(j);
              return (
                <div
                  key={j.job_id}
                  className={`job-card ${inFlight ? "in-flight" : ""}`}
                >
                  <div className="job-card-header">
                    <span className="job-card-icon" aria-hidden>
                      {FORMAT_ICON[j.file_format] ?? "📄"}
                    </span>
                    <div className="job-card-title">
                      <Link
                        href={`/jobs/${j.job_id}`}
                        className="job-card-name"
                        style={{ color: "inherit" }}
                      >
                        {j.source_filename}
                      </Link>
                      <div className="job-card-id">
                        {shortId(j.job_id)} · {timeAgo(j.created_at)}
                      </div>
                    </div>
                    <button
                      className="btn-icon"
                      title="Apagar definitivamente"
                      aria-label={`Apagar ${j.source_filename}`}
                      disabled={inFlight}
                      onClick={() => handleDelete(j)}
                    >
                      ✕
                    </button>
                  </div>

                  <div className="job-card-pills">
                    <StatusBadge status={j.status} />
                    {j.risk_level && <RiskBadge level={j.risk_level} />}
                    <ModeBadge mode={j.mode} />
                  </div>

                  <div className="job-card-actions">
                    {action.href ? (
                      action.download ? (
                        <a
                          className="btn btn-small btn-primary"
                          href={action.href}
                        >
                          ⬇ {action.label}
                        </a>
                      ) : (
                        <Link
                          className="btn btn-small btn-primary"
                          href={action.href}
                        >
                          {action.label} →
                        </Link>
                      )
                    ) : (
                      <span className="muted small">{action.label}</span>
                    )}
                    <span className="spacer" />
                    <Link
                      href={`/jobs/${j.job_id}`}
                      className="btn btn-small btn-ghost"
                    >
                      Detalhes
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
