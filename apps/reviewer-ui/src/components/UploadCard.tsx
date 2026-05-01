"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { uploadJob } from "@/lib/api";
import type { JobMode } from "@/lib/types";

const ACCEPT = ".txt,.md,.rtf,.pdf,.docx,.xlsx,.png,.jpg,.jpeg";

// UI-only third option. The backend only knows the two real JobModes;
// "comparison" reuses the anonymization pipeline and chains the
// diagnostic comparison endpoint after processing finishes.
type UiMode = JobMode | "comparison";

const MODE_DESCRIPTIONS: Record<UiMode, string> = {
  anonymization:
    "Remove ou mascara dados sensíveis. Use quando não precisar restaurar os dados depois.",
  reversible_pseudonymization:
    "Troca dados sensíveis por marcadores. Use quando quiser restaurar os dados originais depois (ex: passar por LLM).",
  comparison:
    "Diagnóstico: compara o que o OPF (modelo) detecta sozinho com o que as regras determinísticas detectam. Não altera o documento — só ajuda a entender quem pegou o quê.",
};

interface UploadCardProps {
  onUploaded: () => void;
}

export function UploadCard({ onUploaded }: UploadCardProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<UiMode>("anonymization");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  async function handleFile(file: File) {
    setError(null);
    setSuccess(null);
    setUploading(true);
    try {
      // The diagnostic mode rides on top of the regular anonymization
      // pipeline — the backend has no separate JobMode for it.
      const backendMode: JobMode =
        mode === "comparison" ? "anonymization" : mode;
      const resp = await uploadJob(file, backendMode);
      if (mode === "comparison") {
        setSuccess(`"${file.name}" enviado · abrindo diagnóstico…`);
        onUploaded();
        // The job detail page reads `autocompare=1` and fires the
        // detector-comparison endpoint as soon as processing finishes.
        router.push(`/jobs/${resp.job_id}?autocompare=1`);
      } else {
        setSuccess(`"${file.name}" enviado · processando…`);
        onUploaded();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="upload-hero">
      <div className="row" style={{ marginBottom: 14, justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>Enviar documento</h2>
        <span className="muted small">Como quer tratar os dados sensíveis?</span>
      </div>

      <div className="mode-tabs">
        <button
          type="button"
          className={`mode-tab ${mode === "anonymization" ? "active" : ""}`}
          onClick={() => setMode("anonymization")}
          disabled={uploading}
        >
          <div className="mode-tab-title">
            <span aria-hidden>🔒</span> Anonimização
          </div>
          <div className="mode-tab-desc">{MODE_DESCRIPTIONS.anonymization}</div>
        </button>
        <button
          type="button"
          className={`mode-tab ${mode === "reversible_pseudonymization" ? "active" : ""}`}
          onClick={() => setMode("reversible_pseudonymization")}
          disabled={uploading}
        >
          <div className="mode-tab-title">
            <span aria-hidden>🔄</span> Pseudonimização reversível
          </div>
          <div className="mode-tab-desc">
            {MODE_DESCRIPTIONS.reversible_pseudonymization}
          </div>
        </button>
        <button
          type="button"
          className={`mode-tab ${mode === "comparison" ? "active" : ""}`}
          onClick={() => setMode("comparison")}
          disabled={uploading}
        >
          <div className="mode-tab-title">
            <span aria-hidden>🔬</span> Comparação de detectores
          </div>
          <div className="mode-tab-desc">{MODE_DESCRIPTIONS.comparison}</div>
        </button>
      </div>

      <div
        className={`dropzone ${dragActive ? "drag-active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          const file = e.dataTransfer.files[0];
          if (file) void handleFile(file);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          disabled={uploading}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
            e.target.value = "";
          }}
          style={{ display: "none" }}
        />
        <div className="dropzone-icon" aria-hidden>📤</div>
        <p className="dropzone-cta">
          Arraste e solte um arquivo aqui ou{" "}
          <button
            type="button"
            className="btn-link"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            escolha um arquivo
          </button>
        </p>
        <p className="dropzone-hint">
          .txt · .md · .rtf · .pdf · .docx · .xlsx · .png · .jpg —
          até 50&nbsp;MB
        </p>
        {uploading && <p className="dropzone-status">⏳ Enviando…</p>}
        {success && !uploading && (
          <p className="dropzone-status success">✅ {success}</p>
        )}
        {error && <p className="dropzone-status error">❌ {error}</p>}
      </div>
    </div>
  );
}
