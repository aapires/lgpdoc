"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { deleteJob, downloadUrl, getJob, getReport } from "@/lib/api";
import type { Job, Report } from "@/lib/types";
import {
  DECISION_LABEL,
  ModeBadge,
  RiskBadge,
  StatusBadge,
} from "@/components/StatusBadge";
import { JobContentPanel } from "@/components/JobContentPanel";
import { DetectorComparisonPanel } from "@/components/DetectorComparisonPanel";
import { ReversiblePanel } from "@/components/ReversiblePanel";

const FORMAT_ICON: Record<string, string> = {
  pdf: "📕",
  docx: "📘",
  xlsx: "📊",
  txt: "📄",
  md: "📝",
};

function formatDate(s: string): string {
  return new Date(s).toLocaleString("pt-BR");
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}

export default function JobDetailPage({
  params,
}: {
  params: { job_id: string };
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const autocompare = searchParams.get("autocompare") === "1";
  const [job, setJob] = useState<Job | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function handleDelete() {
    if (!job) return;
    const ok = window.confirm(
      `Apagar definitivamente "${job.source_filename}"?\n\n` +
        "Esta ação remove o documento original (quarentena), o texto " +
        "redigido, os spans detectados e todo o histórico de revisão. " +
        "Não pode ser desfeita."
    );
    if (!ok) return;
    setBusy(true);
    try {
      await deleteJob(job.job_id);
      router.push("/jobs");
    } catch (e) {
      window.alert(
        `Falha ao apagar: ${e instanceof Error ? e.message : String(e)}`
      );
      setBusy(false);
    }
  }

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const j = await getJob(params.job_id);
        if (!alive) return;
        setJob(j);
        // Report only becomes available once processing finishes; ignore
        // 4xx during pending/processing.
        getReport(params.job_id)
          .then((r) => alive && setReport(r))
          .catch(() => undefined);
        // Keep polling while the job is in flight so the page reflects
        // the transition to awaiting_review without a manual reload.
        if (alive && (j.status === "pending" || j.status === "processing")) {
          pollRef.current = setTimeout(load, 1500);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    }
    load();
    return () => {
      alive = false;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [params.job_id]);

  if (error) return <p className="muted">Falha: {error}</p>;
  if (!job) return <p className="muted">Carregando…</p>;

  const downloadable =
    job.status === "auto_approved" || job.status === "approved";
  const reviewable = !["pending", "processing", "failed"].includes(job.status);
  const inReviewState = job.status === "awaiting_review";
  const isReversible = job.mode === "reversible_pseudonymization";

  return (
    <div>
      <p style={{ marginBottom: 12 }}>
        <Link href="/jobs" className="muted">← voltar para a lista</Link>
      </p>

      {/* Hero */}
      <div className="job-hero">
        <div className="job-hero-top">
          <div className="job-hero-icon" aria-hidden>
            {FORMAT_ICON[job.file_format] ?? "📄"}
          </div>
          <div className="job-hero-info">
            <h1 className="job-hero-name">{job.source_filename}</h1>
            <div className="job-hero-id">{job.job_id}</div>
          </div>
        </div>

        <div className="job-hero-pills">
          <StatusBadge status={job.status} size="lg" />
          {job.risk_level && <RiskBadge level={job.risk_level} size="lg" />}
          <ModeBadge mode={job.mode} size="lg" />
        </div>

        <div className="job-hero-actions">
          {inReviewState && (
            <Link
              href={`/jobs/${job.job_id}/review`}
              className="btn btn-large btn-primary"
            >
              📝 Abrir revisão
            </Link>
          )}
          {!inReviewState && reviewable && (
            <Link
              href={`/jobs/${job.job_id}/review`}
              className="btn btn-large"
            >
              👁 Ver texto / scan manual
            </Link>
          )}
          {downloadable && !isReversible && (
            <a
              className="btn btn-large btn-primary"
              href={downloadUrl(job.job_id)}
            >
              ⬇ Baixar versão anonimizada
            </a>
          )}
          <span className="spacer" style={{ flex: 1 }} />
          {job.error_message && (
            <span style={{ color: "var(--red)" }}>
              ❌ {job.error_message}
            </span>
          )}
        </div>
      </div>

      {/* Document content — text view with marks + manual selection-to-anonymize.
          Always visible once the artefacts are ready, regardless of mode or
          whether any PII was detected automatically. */}
      {report && reviewable && (
        <JobContentPanel
          job={job}
          report={report}
          onUpdated={setReport}
        />
      )}

      {/* Reversible panel — for reversible mode jobs, the full restore workflow. */}
      {isReversible && <ReversiblePanel job={job} />}

      {/* Detector comparison — diagnostic OPF vs regex view. Only available
          once the job has artefacts; never mutates the job's status.
          When the user uploaded with mode=comparison the URL carries
          ?autocompare=1, which makes the panel auto-fire the run as soon
          as the artefacts are ready. */}
      {reviewable && (
        <DetectorComparisonPanel
          jobId={job.job_id}
          autoRun={autocompare}
        />
      )}

      {/* Verification summary (compact) */}
      {report && (
        <div className="card">
          <h2>📊 Resumo da verificação</h2>
          <div
            className="row"
            style={{ flexWrap: "wrap", gap: 24, marginBottom: 6 }}
          >
            <div>
              <div className="muted small">Pontuação</div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>
                {report.risk_assessment.score.toFixed(1)}
              </div>
            </div>
            <div>
              <div className="muted small">Decisão recomendada</div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>
                {DECISION_LABEL[report.risk_assessment.decision] ??
                  report.risk_assessment.decision}
              </div>
            </div>
            <div>
              <div className="muted small">Trechos detectados</div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>
                {report.applied_spans?.length ?? 0}
              </div>
            </div>
            <div>
              <div className="muted small">Sinais residuais</div>
              <div style={{ fontSize: 14 }}>
                {report.residual_spans.length} 2ª passada ·{" "}
                {report.rule_findings.length} regras
              </div>
            </div>
          </div>
          {report.risk_assessment.reasons.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary
                style={{
                  cursor: "pointer",
                  fontSize: 12.5,
                  color: "var(--muted)",
                  fontWeight: 500,
                }}
              >
                Ver motivos detalhados
              </summary>
              <ul style={{ marginTop: 6 }}>
                {report.risk_assessment.reasons.map((r, i) => (
                  <li key={i} className="small">{r}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* Technical details (collapsed) */}
      <details className="collapsible">
        <summary>🔧 Detalhes técnicos</summary>
        <div className="collapsible-body">
          <table>
            <tbody>
              <tr>
                <th>Formato</th>
                <td>{job.file_format}</td>
              </tr>
              <tr>
                <th>Tamanho</th>
                <td>{formatBytes(job.file_size)}</td>
              </tr>
              <tr>
                <th>SHA-256</th>
                <td className="mono small">{job.file_hash}</td>
              </tr>
              <tr>
                <th>Tratamento</th>
                <td>
                  {isReversible
                    ? "🔄 Pseudonimização reversível"
                    : "🔒 Anonimização"}
                </td>
              </tr>
              <tr>
                <th>Decisão do sistema</th>
                <td>
                  {job.decision
                    ? DECISION_LABEL[job.decision] ?? job.decision
                    : "—"}
                </td>
              </tr>
              <tr>
                <th>Risco</th>
                <td>
                  {job.risk_level && <RiskBadge level={job.risk_level} />}{" "}
                  {job.risk_score !== null && (
                    <span className="muted small">
                      pontuação {job.risk_score.toFixed(1)}
                    </span>
                  )}
                </td>
              </tr>
              <tr>
                <th>Enviado em</th>
                <td className="muted">{formatDate(job.created_at)}</td>
              </tr>
              {job.completed_at && (
                <tr>
                  <th>Finalizado em</th>
                  <td className="muted">{formatDate(job.completed_at)}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </details>

      {/* Danger zone */}
      {job.status !== "pending" && job.status !== "processing" && (
        <details className="collapsible">
          <summary style={{ color: "var(--red)" }}>
            ⚠ Zona de exclusão
          </summary>
          <div className="collapsible-body">
            <p className="muted small">
              Remove permanentemente o arquivo original (quarentena), todos os
              artefatos gerados e o histórico de revisão. Não pode ser
              desfeito.
            </p>
            <button
              className="btn btn-danger"
              onClick={handleDelete}
              disabled={busy}
            >
              Excluir definitivamente
            </button>
          </div>
        </details>
      )}
    </div>
  );
}
