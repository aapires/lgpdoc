"use client";

import { useEffect, useState } from "react";

import { getSettings, updateSettings } from "@/lib/api";

interface DetectorMeta {
  kind: string;
  label: string;
  emoji: string;
  category: string;
  hint?: string;
}

// Display metadata. Order here is the order shown in the UI.
const DETECTORS: DetectorMeta[] = [
  // Identity documents
  { kind: "cpf", label: "CPF", emoji: "🪪", category: "Documentos de identidade" },
  { kind: "cnpj", label: "CNPJ", emoji: "🏢", category: "Documentos de identidade" },
  { kind: "rg", label: "RG", emoji: "🆔", category: "Documentos de identidade", hint: "Exige a palavra 'RG' no texto" },
  { kind: "cnh", label: "CNH", emoji: "🚗", category: "Documentos de identidade", hint: "Exige a palavra 'CNH' no texto" },
  { kind: "passaporte", label: "Passaporte", emoji: "🛂", category: "Documentos de identidade" },
  { kind: "titulo_eleitor", label: "Título Eleitor", emoji: "🗳️", category: "Documentos de identidade" },
  { kind: "pis", label: "PIS / NIS", emoji: "📋", category: "Documentos de identidade" },
  { kind: "ctps", label: "CTPS", emoji: "📋", category: "Documentos de identidade" },
  { kind: "sus", label: "Cartão SUS", emoji: "🏥", category: "Documentos de identidade" },
  // Professional registries
  { kind: "oab", label: "OAB", emoji: "⚖️", category: "Registros profissionais" },
  { kind: "crm", label: "CRM", emoji: "🩺", category: "Registros profissionais" },
  { kind: "crea", label: "CREA", emoji: "🔧", category: "Registros profissionais" },
  // Vehicles
  { kind: "placa", label: "Placa Veicular", emoji: "🚘", category: "Veículos" },
  { kind: "renavam", label: "RENAVAM", emoji: "🚘", category: "Veículos" },
  // Legal / fiscal
  { kind: "processo_cnj", label: "Processo CNJ", emoji: "⚖️", category: "Jurídico / fiscal" },
  { kind: "inscricao_estadual", label: "Inscrição Estadual", emoji: "📋", category: "Jurídico / fiscal" },
  // Personal data
  { kind: "private_person", label: "Nome", emoji: "👤", category: "Dados pessoais" },
  { kind: "private_email", label: "E-mail", emoji: "📧", category: "Dados pessoais" },
  { kind: "private_phone", label: "Telefone", emoji: "📞", category: "Dados pessoais" },
  { kind: "cep", label: "CEP", emoji: "📮", category: "Dados pessoais" },
  { kind: "private_address", label: "Endereço", emoji: "📍", category: "Dados pessoais" },
  { kind: "private_date", label: "Data", emoji: "📅", category: "Dados pessoais" },
  // Network / financial
  { kind: "ip", label: "Endereço IP", emoji: "🌐", category: "Outros" },
  { kind: "financeiro", label: "Financeiro", emoji: "💰", category: "Outros" },
  { kind: "account_number", label: "Conta bancária", emoji: "🏦", category: "Outros" },
  { kind: "private_url", label: "URL", emoji: "🔗", category: "Outros" },
  { kind: "secret", label: "Segredo / Token", emoji: "🔑", category: "Outros" },
];

const CATEGORIES = [
  "Documentos de identidade",
  "Registros profissionais",
  "Veículos",
  "Jurídico / fiscal",
  "Dados pessoais",
  "Outros",
];

// ---------------------------------------------------------------------------
// Quick presets — common shapes for "how aggressive should anonymisation be".
// Applying a preset replaces the current selection wholesale; the user can
// still tweak the result individually before clicking "Salvar".
// ---------------------------------------------------------------------------

interface Preset {
  id: string;
  label: string;
  emoji: string;
  description: string;
  // The internal kind list. Items not in ``available`` (reported by the
  // backend) are silently dropped at apply time, so older deployments
  // don't break when new kinds are added to the preset definition.
  kinds: string[];
}

// Common base — used as the seed for "Leve" and explicitly extended by
// "Intermediário".
const PRESET_LEVE_KINDS = [
  "private_person",  // Nome
  "cpf",
  "cnpj",
  "rg",
  "cnh",
  "private_email",   // E-mail
  "oab",
  "crm",
  "crea",
  "placa",           // Placa Veicular
  "secret",          // Segredo / Token
];

const PRESET_INTERMEDIARIO_KINDS = [
  ...PRESET_LEVE_KINDS,
  "private_company", // Empresa / Órgão
  "renavam",
  "titulo_eleitor",
  "passaporte",
  "pis",             // PIS / NIS
  "ctps",
  "sus",             // Cartão SUS
  "inscricao_estadual",
  "private_phone",   // Telefone
  "cep",
  "private_address", // Endereço
];

// "Pesado" = everything except low-signal-for-most-cases types.
// The set is computed dynamically from ``available`` at apply time so it
// stays correct even if new kinds land in the backend.
const PRESET_PESADO_EXCLUDE = [
  "private_date",
  "financeiro",
  "private_url",
  "ip",
];

const PRESETS: Preset[] = [
  {
    id: "leve",
    label: "Tratamento leve",
    emoji: "🟢",
    description:
      "Identificadores essenciais — Nome, CPF, CNPJ, RG, CNH, " +
      "E-mail, OAB/CRM/CREA, Placa, Segredo/Token.",
    kinds: PRESET_LEVE_KINDS,
  },
  {
    id: "intermediario",
    label: "Intermediário",
    emoji: "🟡",
    description:
      "Tudo do leve + Empresa/Órgão, Renavam, Título de Eleitor, " +
      "Passaporte, PIS/NIS, CTPS, Cartão SUS, Inscrição Estadual, " +
      "Telefone, CEP, Endereço.",
    kinds: PRESET_INTERMEDIARIO_KINDS,
  },
  {
    id: "pesado",
    label: "Pesado",
    emoji: "🟠",
    description:
      "Marca todos os tipos exceto Data, Financeiro, URL e IP.",
    // Empty here — computed from ``available`` at apply time.
    kinds: [],
  },
  {
    id: "critica",
    label: "Crítico",
    emoji: "🔴",
    description:
      "Marca todos os tipos disponíveis. Máxima proteção, " +
      "potencial de falsos positivos maior.",
    kinds: [],
  },
];

export default function SettingsPage() {
  const [enabled, setEnabled] = useState<Set<string> | null>(null);
  const [available, setAvailable] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setEnabled(new Set(s.enabled_detectors));
        setAvailable(s.available_detectors);
      })
      .catch((e) => setError(String(e)));
  }, []);

  function toggle(kind: string) {
    if (!enabled) return;
    const next = new Set(enabled);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    setEnabled(next);
  }

  function selectAll() {
    if (!enabled) return;
    setEnabled(new Set(available));
  }

  function clearAll() {
    setEnabled(new Set());
  }

  function applyPreset(presetId: string) {
    if (!enabled) return;
    const availableSet = new Set(available);
    let kinds: string[];
    if (presetId === "critica") {
      // Everything the backend reports as available.
      kinds = available;
    } else if (presetId === "pesado") {
      // All available, minus the explicit exclude list.
      kinds = available.filter(
        (k) => !PRESET_PESADO_EXCLUDE.includes(k)
      );
    } else {
      const preset = PRESETS.find((p) => p.id === presetId);
      if (!preset) return;
      // Drop kinds the backend doesn't report — defensive against
      // older deployments where a preset entry doesn't exist.
      kinds = preset.kinds.filter((k) => availableSet.has(k));
    }
    setEnabled(new Set(kinds));
    setSavedAt(null); // changes are pending, force user to hit Save
  }

  async function save() {
    if (!enabled) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await updateSettings(Array.from(enabled));
      setEnabled(new Set(updated.enabled_detectors));
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && enabled === null) {
    return <p className="muted">Falha ao carregar: {error}</p>;
  }
  if (enabled === null) return <p className="muted">Carregando…</p>;

  // Group detectors by category, but only show kinds the backend reports
  // as available (so disabled features don't pollute the UI).
  const availableSet = new Set(available);
  const visible = DETECTORS.filter((d) => availableSet.has(d.kind));
  const byCategory: Record<string, DetectorMeta[]> = {};
  for (const cat of CATEGORIES) byCategory[cat] = [];
  for (const d of visible) {
    (byCategory[d.category] ??= []).push(d);
  }

  return (
    <div>
      <div className="page-title">
        <div>
          <h1>Configurações</h1>
          <p className="page-subtitle">
            Selecione quais tipos de dado o sistema deve identificar.
            Mudanças valem para os próximos uploads — documentos já
            processados mantêm os spans existentes.
          </p>
        </div>
      </div>

      <div className="card">
        <h2 style={{ fontSize: 14, marginBottom: 8 }}>Ajustes rápidos</h2>
        <p className="muted small" style={{ marginBottom: 12 }}>
          Atalhos para perfis comuns. Aplicar um perfil substitui a
          seleção atual; você ainda pode ajustar item a item antes de
          salvar.
        </p>
        <div className="settings-presets">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className="settings-preset-btn"
              onClick={() => applyPreset(p.id)}
              disabled={busy}
              title={p.description}
            >
              <span className="settings-preset-emoji">{p.emoji}</span>
              <span className="settings-preset-label">{p.label}</span>
              <span className="settings-preset-desc">{p.description}</span>
            </button>
          ))}
        </div>

        <div
          className="row"
          style={{
            margin: "16px 0",
            justifyContent: "space-between",
            paddingTop: 16,
            borderTop: "1px solid var(--border)",
          }}
        >
          <div className="btn-row">
            <button className="btn btn-small" onClick={selectAll} disabled={busy}>
              Marcar todos
            </button>
            <button className="btn btn-small" onClick={clearAll} disabled={busy}>
              Desmarcar todos
            </button>
          </div>
          <span className="pill pill-info">
            {enabled.size} de {visible.length} ativos
          </span>
        </div>

        {CATEGORIES.map((cat) => {
          const list = byCategory[cat];
          if (!list || list.length === 0) return null;
          return (
            <div key={cat} style={{ marginBottom: 16 }}>
              <h2 style={{ fontSize: 14, marginBottom: 8 }}>{cat}</h2>
              <div className="settings-grid">
                {list.map((d) => {
                  const isOn = enabled.has(d.kind);
                  return (
                    <label
                      key={d.kind}
                      className={`settings-item ${isOn ? "on" : "off"}`}
                    >
                      <input
                        type="checkbox"
                        checked={isOn}
                        onChange={() => toggle(d.kind)}
                        disabled={busy}
                      />
                      <span className="settings-emoji">{d.emoji}</span>
                      <span className="settings-label">
                        {d.label}
                        {d.hint && (
                          <span className="muted small" style={{ display: "block" }}>
                            {d.hint}
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}

        <div
          className="btn-row"
          style={{
            marginTop: 16,
            paddingTop: 16,
            borderTop: "1px solid var(--border)",
          }}
        >
          <button className="btn btn-primary" onClick={save} disabled={busy}>
            {busy ? "Salvando…" : "Salvar configurações"}
          </button>
          {savedAt && !busy && (
            <span className="muted" style={{ marginLeft: 8 }}>
              ✓ salvo
            </span>
          )}
          {error && (
            <span style={{ color: "var(--red)", marginLeft: 8 }}>
              {error}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
