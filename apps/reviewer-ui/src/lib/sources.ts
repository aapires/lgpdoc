/**
 * Helpers to translate the technical ``source`` value of a span into
 * something the reviewer can read. Used by the review UI to display
 * provenance per-span and to compute the diagnostic summary at the top.
 */

export type SourceCategory = "model" | "regex" | "manual" | "unknown";

export interface SourceMeta {
  category: SourceCategory;
  label: string;
  short: string;
  emoji: string;
}

const SOURCE_TABLE: Record<string, SourceMeta> = {
  openai_privacy_filter: {
    category: "model",
    label: "Modelo OpenAI Privacy Filter",
    short: "OPF",
    emoji: "🤖",
  },
  manual: {
    category: "manual",
    label: "Anonimização manual",
    short: "Manual",
    emoji: "✋",
  },
  // Regex / deterministic detectors
  br_cpf: { category: "regex", label: "Regra: CPF (DV validado)", short: "CPF", emoji: "🪪" },
  br_cnpj: { category: "regex", label: "Regra: CNPJ (DV validado)", short: "CNPJ", emoji: "🏢" },
  br_labeled_name: { category: "regex", label: "Regra: nome rotulado (Cliente, Réu, ...)", short: "Nome rotulado", emoji: "👤" },
  br_logradouro: { category: "regex", label: "Regra: logradouro (Rua, Av., Praça, ...)", short: "Logradouro", emoji: "📍" },
  br_unidade: { category: "regex", label: "Regra: unidade (Apto, Bloco, Torre, ...)", short: "Unidade", emoji: "🏠" },
  br_rg: { category: "regex", label: "Regra: RG", short: "RG", emoji: "🆔" },
  br_cnh: { category: "regex", label: "Regra: CNH", short: "CNH", emoji: "🚗" },
  br_passaporte: { category: "regex", label: "Regra: Passaporte", short: "Passaporte", emoji: "🛂" },
  br_titulo: { category: "regex", label: "Regra: Título de Eleitor", short: "Título", emoji: "🗳️" },
  br_pis: { category: "regex", label: "Regra: PIS / NIS", short: "PIS", emoji: "📋" },
  br_ctps: { category: "regex", label: "Regra: CTPS", short: "CTPS", emoji: "📋" },
  br_sus: { category: "regex", label: "Regra: Cartão SUS", short: "SUS", emoji: "🏥" },
  br_oab: { category: "regex", label: "Regra: OAB", short: "OAB", emoji: "⚖️" },
  br_crm: { category: "regex", label: "Regra: CRM", short: "CRM", emoji: "🩺" },
  br_crea: { category: "regex", label: "Regra: CREA", short: "CREA", emoji: "🔧" },
  br_placa: { category: "regex", label: "Regra: Placa", short: "Placa", emoji: "🚘" },
  br_renavam: { category: "regex", label: "Regra: RENAVAM", short: "RENAVAM", emoji: "🚘" },
  br_cnj: { category: "regex", label: "Regra: Processo CNJ", short: "Processo", emoji: "⚖️" },
  br_ie: { category: "regex", label: "Regra: Inscrição Estadual", short: "I.E.", emoji: "📋" },
  br_cep: { category: "regex", label: "Regra: CEP", short: "CEP", emoji: "📮" },
  ipv4: { category: "regex", label: "Regra: Endereço IP", short: "IP", emoji: "🌐" },
  brl_amount: { category: "regex", label: "Regra: Valor monetário (R$)", short: "Financeiro", emoji: "💰" },
  // Legal entities (companies + government bodies)
  br_company_suffix: { category: "regex", label: "Regra: empresa com sufixo (Ltda, S.A., EIRELI, ...)", short: "Empresa", emoji: "🏛" },
  br_gov_body: { category: "regex", label: "Regra: órgão público (Ministério, Secretaria, ...)", short: "Órgão", emoji: "🏛" },
  br_edu_institution: { category: "regex", label: "Regra: instituição de ensino (Universidade, Faculdade, ...)", short: "Educação", emoji: "🎓" },
  br_date: { category: "regex", label: "Regra: data BR (25/12/2024, 25 de dezembro de 2024, ...)", short: "Data", emoji: "📅" },
  // Mock client (used in dev/tests)
  mock: { category: "regex", label: "Mock regex (modo desenvolvimento)", short: "Mock", emoji: "🧪" },
};

export function sourceMeta(source: string | null | undefined): SourceMeta {
  if (!source) {
    return {
      category: "unknown",
      label: "Origem desconhecida",
      short: "?",
      emoji: "❓",
    };
  }
  return (
    SOURCE_TABLE[source] ?? {
      category: "regex",
      label: `Regra: ${source}`,
      short: source.replace(/^br_/, ""),
      emoji: "📋",
    }
  );
}

export interface DetectionStats {
  total: number;
  model: number;
  regex: number;
  manual: number;
  unknown: number;
  bySource: { source: string; count: number; meta: SourceMeta }[];
  byEntityCategory: { category: SourceCategory; entities: Record<string, number> };
}

export function computeStats(
  spans: Array<{ source?: string | null; entity_type: string; false_positive?: boolean }>
): DetectionStats {
  const stats: DetectionStats = {
    total: 0,
    model: 0,
    regex: 0,
    manual: 0,
    unknown: 0,
    bySource: [],
    byEntityCategory: { category: "model", entities: {} },
  };
  const sourceCounts: Record<string, number> = {};
  for (const s of spans) {
    if (s.false_positive) continue; // reverted spans don't count
    stats.total += 1;
    const m = sourceMeta(s.source);
    stats[m.category] += 1;
    const key = s.source ?? "unknown";
    sourceCounts[key] = (sourceCounts[key] ?? 0) + 1;
  }
  stats.bySource = Object.entries(sourceCounts)
    .map(([source, count]) => ({ source, count, meta: sourceMeta(source) }))
    .sort((a, b) => b.count - a.count);
  return stats;
}
