"use client";

interface Props {
  // ``true`` = OPF was active when the job ran; ``false`` = regex only;
  // ``null`` = legacy job from before this column was tracked.
  opfUsed: boolean | null;
}

export function OpfModeBadge({ opfUsed }: Props) {
  if (opfUsed === null || opfUsed === undefined) {
    // Legacy rows — don't badge anything to avoid looking like a
    // confirmed "regex only" decision.
    return null;
  }
  if (opfUsed) {
    return (
      <span
        className="badge badge-opf-on"
        title="Este documento foi processado com o OpenAI Privacy Filter ativo (modelo + regras)."
      >
        🤖 OPF
      </span>
    );
  }
  return (
    <span
      className="badge badge-opf-off"
      title="Este documento foi processado sem o OPF (apenas regras determinísticas)."
    >
      📋 só regex
    </span>
  );
}
