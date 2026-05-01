import type { JobMode, JobStatusValue, RiskLevel } from "@/lib/types";

const STATUS_META: Record<
  JobStatusValue,
  { cls: string; label: string; icon: string }
> = {
  pending:           { cls: "pill-neutral",  label: "pendente",                icon: "⏳" },
  processing:        { cls: "pill-neutral",  label: "processando",             icon: "⚙️" },
  awaiting_review:   { cls: "pill-medium",   label: "aguardando revisão",      icon: "🟡" },
  auto_approved:     { cls: "pill-low",      label: "aprovado automaticamente", icon: "✓" },
  approved:          { cls: "pill-low",      label: "aprovado",                icon: "✓" },
  rejected:          { cls: "pill-high",     label: "rejeitado",               icon: "✗" },
  failed:            { cls: "pill-critical", label: "falhou",                  icon: "⚠" },
};

export function StatusBadge({
  status,
  size = "default",
}: {
  status: JobStatusValue | string;
  size?: "default" | "lg";
}) {
  const meta =
    STATUS_META[status as JobStatusValue] ?? {
      cls: "pill-neutral",
      label: status,
      icon: "•",
    };
  return (
    <span
      className={`pill ${meta.cls}`}
      style={size === "lg" ? { padding: "5px 14px", fontSize: 13 } : undefined}
    >
      <span aria-hidden>{meta.icon}</span> {meta.label}
    </span>
  );
}

const RISK_META: Record<
  NonNullable<RiskLevel>,
  { cls: string; label: string; icon: string }
> = {
  low:      { cls: "pill-low",      label: "baixo",   icon: "🟢" },
  medium:   { cls: "pill-medium",   label: "médio",   icon: "🟡" },
  high:     { cls: "pill-high",     label: "alto",    icon: "🟠" },
  critical: { cls: "pill-critical", label: "crítico", icon: "🔴" },
};

export function RiskBadge({
  level,
  size = "default",
}: {
  level: RiskLevel;
  size?: "default" | "lg";
}) {
  if (!level) return <span className="pill pill-neutral">sem risco</span>;
  const meta = RISK_META[level];
  return (
    <span
      className={`pill ${meta.cls}`}
      style={size === "lg" ? { padding: "5px 14px", fontSize: 13 } : undefined}
    >
      <span aria-hidden>{meta.icon}</span> Risco {meta.label}
    </span>
  );
}

export function ModeBadge({
  mode,
  size = "default",
}: {
  mode: JobMode | string;
  size?: "default" | "lg";
}) {
  const isReversible = mode === "reversible_pseudonymization";
  return (
    <span
      className={`pill ${isReversible ? "pill-info" : "pill-neutral"}`}
      style={size === "lg" ? { padding: "5px 14px", fontSize: 13 } : undefined}
    >
      <span aria-hidden>{isReversible ? "🔄" : "🔒"}</span>{" "}
      {isReversible ? "Reversível" : "Anonimização"}
    </span>
  );
}

export const DECISION_LABEL: Record<string, string> = {
  auto_approve: "aprovação automática",
  sample_review: "revisão por amostragem",
  manual_review: "revisão manual",
};
