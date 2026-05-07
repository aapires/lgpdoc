---
tags: [lgpdoc, settings, presets, runtime]
---

# Configurações

Duas camadas de configuração:

1. **Settings (`Settings` class)** — variáveis de ambiente, valor fixo no boot. Vê `src/anonymizer_api/config.py`.
2. **Runtime config (`SettingsStore`)** — quais detectores estão habilitados, mutável via UI sem restart. Persistido em `var/runtime_config.json`.

## Settings (boot-time)

Lidas de variáveis de ambiente com prefixo `ANONYMIZER_API_`:

| Setting | Default | O que faz |
|---|---|---|
| `quarantine_dir` | `./var/quarantine` | Onde ficam os arquivos originais. |
| `output_dir` | `./var/output` | Artefatos por job. |
| `db_url` | `sqlite:///./var/anonymizer_api.db` | Banco. |
| `max_bytes` | 50 MiB | Limite de upload. |
| `policy_path` | `./policies/default.yaml` | Política de redação. |
| `runtime_config_path` | `./var/runtime_config.json` | SettingsStore. |
| `use_mock_client` | `false` | `true` força modo mock — OPF unavailable. |
| `opf_use_mock_worker` | `false` | Tests-only — subprocesso usa MockClient em vez do OPF real. |
| `opf_idle_timeout_seconds` | `300` | Auto-disable do OPF. `0` desabilita o watchdog. Vê [[06 - OPF runtime toggle]]. |
| `cors_origins` | `["http://localhost:3000", "http://127.0.0.1:3000"]` | CORS allowlist. |

Override em runtime via env:

```bash
ANONYMIZER_API_OPF_IDLE_TIMEOUT_SECONDS=600 ./start-anom.sh
```

## Runtime config — `SettingsStore`

`src/anonymizer_api/settings_store.py`. Thread-safe, cacheado em memória. Persiste como JSON.

API:

```python
store.get() → dict
store.update(enabled_detectors=[...]) → dict
store.get_enabled_kinds() → set[str]
```

Cada `entity_type` (chave de `policies/default.yaml`) pode estar **enabled** ou **disabled**. Detectores desabilitados ainda **rodam** mas seus spans são filtrados pelo `_OverridingComposite` antes de chegar ao redactor.

## Presets

A UI em `/settings` oferece presets prontos:

| Preset | Foco |
|---|---|
| **Leve** | PII básica — pessoa, e-mail, telefone, CPF. Pra documentos onde compliance é leve. |
| **Intermediário** | Leve + CNPJ, RG, OAB, CRM, datas, endereços, empresas. Default razoável. |
| **Pesado** | Intermediário + CNH, passaporte, título eleitoral, PIS, processo CNJ, IP, RENAVAM, etc. Quase tudo. |
| **Crítica** | Pesado + tipos de baixa frequência mas alto risco regulatório. |

Aplicar um preset = `PUT /settings` com a lista de detectores correspondente. Persistido no `runtime_config.json`.

> [!info] Preset não é um modo
> Não há `Settings.preset = "intermediario"`. Preset é só açúcar de UI — o que persiste é a lista de detectores habilitados.

## Política de redação

`policies/default.yaml` mapeia `entity_type` → estratégia + label:

```yaml
private_person:
  strategy: indexed       # gera [PESSOA_NN]
  label: PESSOA
private_email:
  strategy: indexed
  label: EMAIL
cpf:
  strategy: indexed
  label: CPF
private_address:
  strategy: replace
  label: "[ENDEREÇO]"
```

Estratégias: vê [[04 - Pipeline de detecção#Redact — estratégias]].

> [!warning] Política não afeta jobs já processados
> Editou `default.yaml`? A mudança vale só para uploads novos. Para reprocessar com a política nova, use o botão **Reprocessar** no review (`POST /jobs/{id}/reprocess`).

## Reprocess — re-rodar com configurações atuais

`POST /jobs/{id}/reprocess` apaga os artefatos antigos do job (mantém o original em quarentena) e re-roda o pipeline pegando:

- A política atual em disco.
- Os detectores habilitados via SettingsStore.
- O estado **atual** do toggle OPF (ON ou OFF).

Use quando descobre na revisão que precisa de tratamento mais agressivo. Não há diff entre runs — sobrescreve.

> [!warning] Apaga eventos de revisão
> Reprocess limpa os `review_events` daquele job. Comentários, "false positive" marcados, edits manuais — tudo vai. O `detector_comparison.json` é preservado (única exceção).

## Onde está o quê

| Arquivo | Conteúdo |
|---|---|
| `Settings` env vars | `src/anonymizer_api/config.py` |
| Runtime store | `src/anonymizer_api/settings_store.py` + `var/runtime_config.json` |
| Política | `policies/default.yaml` |
| Endpoint UI | `src/anonymizer_api/routers/settings.py` |
| Componente UI | `apps/reviewer-ui/src/app/settings/page.tsx` |

Próximo: [[11 - API]].
