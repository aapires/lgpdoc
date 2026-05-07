---
tags: [lgpdoc, arquitetura]
---

# Arquitetura

Três camadas, processo único:

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend — Next.js 14 (porta 3000)                          │
│  apps/reviewer-ui/                                           │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (fetch)
┌──────────────────────────▼──────────────────────────────────┐
│  Backend — FastAPI (porta 9000)                              │
│  src/anonymizer_api/                                         │
│                                                              │
│   routers/  jobs.py · containers.py · settings.py · opf.py   │
│             · detector_comparison.py                         │
│   service:  JobService · ContainerService                    │
│   storage:  SQLite (var/anonymizer_api.db) + filesystem      │
└──────────────────────────┬──────────────────────────────────┘
                           │ Python calls
┌──────────────────────────▼──────────────────────────────────┐
│  Core — anonymizer (puro Python, sem Flask/Django)           │
│  src/anonymizer/                                             │
│                                                              │
│   pipeline.py        DocumentPipeline                        │
│   extractors/        TxtExtractor, PdfExtractor, ...         │
│   client.py          PrivacyFilterClient (ABC)               │
│   augmentations.py   make_augmented_client + BR detectors    │
│   regex_detectors.py REGEX_DETECTORS dict                    │
│   redactor.py        Redactor (stateful per doc)             │
│   verification.py    Verifier (segunda passada)              │
│   detector_comparison.py  diagnostic mode                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ stdin/stdout JSON
┌──────────────────────────▼──────────────────────────────────┐
│  Subprocesso OPF (opcional, sob demanda)                     │
│  scripts/opf_worker.py                                       │
│                                                              │
│  OpenAIPrivacyFilterClient → opf.OPF → torch                 │
│                                                              │
│  Sobe quando o usuário liga o toggle. Morre quando ele       │
│  desliga (manualmente ou via watchdog idle).                 │
│  Detalhes em [[06 - OPF runtime toggle]].                    │
└─────────────────────────────────────────────────────────────┘
```

## Pacotes Python

| Pacote | Propósito |
|---|---|
| `anonymizer` | Core puro — detecção/redação/verificação. Sem dependência de FastAPI. |
| `anonymizer_api` | Fachada FastAPI sobre o core. Banco, jobs, containers, OPF runtime, auth (futura). |

> [!info] Por que dois pacotes?
> Manter o core sem dependência de framework permite reusar a redação em CLIs (`scripts/anonymize_document.py`), em testes integrados, ou em qualquer outra fachada que apareça (Slack bot, queue worker, etc).

## Inversão de dependências

- `anonymizer_api` importa `anonymizer`. Nunca o contrário.
- `anonymizer_api/containers/` não importa `anonymizer_api/jobs/` (e vice-versa). A separação é **enforced por testes** em `tests/test_mode_separation.py`.
- O subprocesso OPF é fronteira firme: o pacote `opf` (e `torch`) **só** existem dentro de `scripts/opf_worker.py`. Importar `anonymizer_api` numa máquina sem torch funciona.

## Fluxos principais

- **Upload de documento avulso** — `POST /jobs/upload` → background task → `JobService.process()` → [[04 - Pipeline de detecção]] → status `awaiting_review`.
- **Container com múltiplos documentos** — `POST /api/containers` → upload de docs → fluxo de revisão por documento → tabela de marcadores compartilhada. Vê [[08 - Containers]].
- **Reprocesso** — `POST /jobs/{id}/reprocess` → reset estado → re-roda pipeline com configurações atuais. Vê [[10 - Configurações]].
- **Comparação de detectores** — `POST /jobs/{id}/detector-comparison` → roda OPF e regex separadamente, gera relatório. Não altera o job. Vê [[09 - Modo de comparação]].
- **Pseudonimização reversível** — modo escolhido no upload → marcadores indexados → endpoints `/reversible/*` para validar e restaurar. Vê [[07 - Modos de processamento]].

## Persistência

- **SQLite** em `var/anonymizer_api.db`. Detalhes em [[12 - Banco de dados]].
- **Arquivos**:
  - `var/quarantine/<job_id>.<ext>` — original.
  - `var/output/<job_id>/redacted.txt` — texto redigido.
  - `var/output/<job_id>/spans.json` — spans aplicados (com offsets).
  - `var/output/<job_id>/report.json` — verificação + risco.
  - `var/output/<job_id>/detector_comparison.json` — relatório do modo diagnóstico.

## Lifecycle do FastAPI

```python
# src/anonymizer_api/main.py — create_app()
storage         = Storage(...)
database        = Database(...).create_all()
settings_store  = SettingsStore(...)
opf_manager     = OPFManager(available=..., idle_timeout_seconds=300)
client          = make_augmented_client(ToggledBaseClient(opf_manager), ...)
service_factory = lambda db: JobService(...)

app.state.{storage, database, client, opf_manager, settings_store, service_factory}
```

`@asynccontextmanager` no `lifespan` garante `opf_manager.shutdown()` no encerramento — para o watchdog e mata o subprocesso.

Próximo: [[04 - Pipeline de detecção]].
