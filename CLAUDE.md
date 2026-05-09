# LGPDoc — instruções para Claude Code

Produto: **LGPDoc** (codinome interno do pacote Python: `anonymizer` —
não renomear, mantém histórico e imports estáveis).

Pipeline local de anonimização de documentos. OPF (OpenAI Privacy Filter) +
augmentações regex BR + verificação por segunda passada, com FastAPI por trás
e UI em Next.js para revisão.

## ⚠️ Leia antes de implementar

**`docs/project-context.md`** é a fonte de verdade — convenções, padrões,
versões exatas e regras "critical don't-miss". Releia ao começar uma
tarefa que mexa em código.

Subdocumentos úteis:
- `docs/local_setup.md` — setup do venv, OPF, modelo
- `docs/pipeline.md` — fluxo de extração/redação/verificação
- `documentacao-obsidian/` — wiki em formato Obsidian (PT-BR, ~18 notas
  cross-linkadas). Bom ponto de partida para entender o produto.
- `README.md` — face pública do repositório (https://github.com/aapires/lgpdoc).

## Lembretes que valem para qualquer tarefa

- **Privacidade**: NUNCA logar texto bruto, fragmentos PII ou substituições.
  Apenas metadados (job_id, hash, posições, entity_type, score).
- **venv obrigatório**: `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/pip`.
  Nunca usar Python global.
- **Idioma**: mensagens de código/log em **inglês**, mensagens visíveis ao
  usuário (UI, toasts, banners) em **PT-BR**.
- **PEP 604 sempre**: `str | None`, `list[X]`, `dict[K, V]`. Sem `Optional`,
  `List`, `Dict` do `typing`.
- **TypeScript**: App Router, `"use client"` em interativos, path alias `@/`,
  CSS plain (sem Tailwind/Material/etc.).
- **Status `blocked` foi removido**: tudo crítico vai para `awaiting_review`
  com `risk_level=critical`.
- **TODO documento processado vai para `awaiting_review`**, mesmo low risk.
  `decision` e `risk_level` são apenas sinais visuais; nunca pulam revisão.
- **Redação manual usa find-and-replace-all** com `expected_text` como fonte
  de verdade — não confiar só em offsets.
- **Spans carregam `redacted_start`/`redacted_end` autoritativos** (definidos
  pelo backend). Frontend lê direto, não recalcula.
- **Datetimes**: serializar com `+00:00` explícito (SQLite perde tzinfo).
- **Modo "Comparação de detectores"** é diagnóstico, não JobMode: a 3ª tab
  do upload sobe como `anonymization` e dispara `/detector-comparison`
  via `?autocompare=1`. NUNCA muda `status`/`decision`/artefatos do job.
  O endpoint **auto-carrega o OPF** (`ensure_loaded()`) — comparação sem
  o lado-modelo é nonsense. Após a chamada, o toggle do header está ON.
- **OPF da comparação** = `CaseNormalizingClient(base)`, sem augmentações
  regex (essas são o lado `RegexOnlyClient`). Manter assim — misturar
  inverte o sinal do diagnóstico.
- **OPF é runtime toggle, não boot-time**. Subprocesso isolado em
  `scripts/opf_worker.py`. `OPFManager` (em `app.state.opf_manager`)
  gerencia lifecycle + lease/refcount + watchdog idle (default 5 min,
  configurável via `Settings.opf_idle_timeout_seconds`; `0` desabilita).
  Disable mata o subprocesso → SO recupera ~3 GB integralmente.
- **Snapshot por job**: `JobService.process()` faz `acquire()` no início
  e `release()` no fim. Toggle desligado mid-job NÃO afeta o job em vôo.
  `opf_used` é capturado nesse momento e persistido na linha do job.
- **`RegexFallbackClient` é o fallback de produção** quando OPF está OFF
  (apenas e-mail, conservador). `MockPrivacyFilterClient` é só para
  testes — tem regex heurística de nome que produzia falsos positivos
  massivos em ALL-CAPS BR.
- **Tests: use `opf_use_mock_worker=True`** quando o teste exercita
  funcionalidades que dependem de OPF "available" (badges `opf_used`,
  detector comparison, reprocess com OPF on). `use_mock_client=True`
  desabilita OPF inteiramente — toggle some, comparação retorna 409.

## Subir o ambiente

```bash
./start-anom.sh                 # OPF real + UI em http://localhost:3000
./start-anom.sh --mock          # regex mode (sem download de modelo)
./start-anom.sh --reset         # zera ./var/ antes de subir
```

## Validação antes de declarar "feito"

```bash
.venv/bin/pytest -q                                        # 626 testes
cd apps/reviewer-ui && npx --no-install next build         # build TS
```

Os dois precisam estar verdes.

## Convenções de tarefa

- **Não fazer git commits sem pedido explícito** do usuário. Push é um
  ato separado — pedido explícito também ("commit e push").
- **Não criar arquivos de documentação** (`*.md`, READMEs) a menos que o
  usuário peça. Editar os existentes quando fizer sentido.
- **Testes novos** devem cobrir caminho feliz + ≥1 caso de erro. Apenas
  fixtures sintéticas — sem PII real.
- **Reprocessar um job** = `POST /jobs/{id}/reprocess`. Apaga artefatos,
  preserva o original em quarentena, re-roda com configurações **atuais**
  (settings, política, estado do toggle OPF). Botão "🔁 Reprocessar"
  está na tela de revisão.

## Estrutura

```
src/anonymizer/         core: redactor, policy, augmentations,
                        regex_detectors, regex_only_client,
                        regex_fallback_client, subprocess_opf_client,
                        detector_comparison
src/anonymizer_api/     FastAPI: routers, db, jobs/, containers/,
                        opf_manager, schemas, settings_store
apps/reviewer-ui/       Next.js 14 App Router (TS)
policies/default.yaml   política de redação (entity_type → strategy + label)
docs/                   project-context.md, local_setup.md, pipeline.md
documentacao-obsidian/  wiki em formato Obsidian (PT-BR)
tests/                  pytest, organizado por módulo (~626 casos)
scripts/                CLIs + opf_worker.py (subprocesso OPF)
bootstrap.sh            instalador macOS (Homebrew + clone + venv + npm)
start-anom.sh           sobe API + UI; flags --mock --reset --no-ui
README.md, LICENSE      face pública (MIT)
var/                    runtime state (NÃO versionado): quarantine/, output/, db
```
