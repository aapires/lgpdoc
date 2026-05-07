---
tags: [lgpdoc, dev, workflow, conventions]
---

# Desenvolvimento — workflow e convenções

Esta nota é para quem mexe no código. Espelha (resumidamente) `docs/project-context.md` e `CLAUDE.md`, com foco operacional.

## Setup

Vê [[02 - Instalação]]. Em resumo:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev,api,ocr]"     # core
.venv/bin/pip install -e ".[dev,api,ocr,ml]"  # com OPF (~3 GB de modelo na 1ª exec)
cd apps/reviewer-ui && npm install
```

> [!warning] venv obrigatório
> **Sempre** usar `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/pip`. Nunca Python global.

## Subir tudo

```bash
./start-anom.sh                 # API + UI, OPF real
./start-anom.sh --mock          # regex apenas (sem download)
./start-anom.sh --reset         # zera ./var/ antes
./start-anom.sh --no-ui         # só backend
./start-anom.sh --port 8080     # porta API custom
./start-anom.sh --no-ocr-setup  # pula brew/pip de OCR
```

## Validação antes de declarar feito

```bash
.venv/bin/pytest -q                                  # 626 testes
cd apps/reviewer-ui && npx --no-install next build   # build TS
```

Os dois precisam estar verdes. Vê [[15 - Testes]].

## Convenções Python

- **Python 3.11+** — type hints PEP 604 sempre (`str | None`, `list[X]`, `dict[K, V]`). NÃO importar `Optional`, `List`, `Dict`, `Tuple`, `Set` de `typing`.
- **`from __future__ import annotations`** no topo de todo módulo.
- **Layout `src/`** — pacotes acessíveis como `anonymizer` e `anonymizer_api` após `pip install -e .`.
- **Logger por módulo**: `logger = logging.getLogger(__name__)`. Nunca `print` ou `logging.info` direto.
- **f-strings** sempre. Nunca `%` ou `.format()`.
- **`pathlib.Path`** > `os.path` em todo lugar.
- **Encoding explícito** ao ler arquivos: `Path.read_text(encoding="utf-8")`.
- **Imports pesados são lazy** quando opcionais (`opf`, `torch`, `pypdf` etc) — dentro do método que precisa, com `try/except ImportError` produzindo `RuntimeError` explicativo.
- **Erros internos sobem** — só validar em fronteiras (entrada do usuário, API externa). Sem try/except defensivo no meio.

## Convenções TypeScript

- **Next.js App Router** (`src/app/`). Nunca `pages/`.
- **`"use client"` no topo** de qualquer componente que use hooks ou `window.*`.
- **Path alias `@/`** → `./src/`. Nunca `../../../`.
- **Strict mode** — `any` proibido, use `unknown` se necessário.
- **Tipos em `src/lib/types.ts`** espelhando os schemas Pydantic.
- **Cliente API em `src/lib/api.ts`**. Toda chamada passa por lá.
- **CSS plain via `className`**. `style={...}` inline só para valores dinâmicos.

## Convenções de mensagens

> [!info] Idioma
> - **Mensagens de erro no código + logs em inglês**.
> - **Mensagens visíveis ao usuário em PT-BR** (toasts, banners, labels, tooltips).

## Status `blocked` foi removido

> [!warning] Não existe mais
> Conteúdo crítico (JWT, secret, CPF etc) **não vai** mais para `blocked` — vai para `awaiting_review` com `risk_level=critical`. Decisões possíveis: `auto_approve`, `sample_review`, `manual_review`. Daí em diante o reviewer humano decide.

## Spans — autoridade dos offsets

> [!warning] redacted_start / redacted_end são autoritativos
> Spans carregam `redacted_start`/`redacted_end` definidos pelo backend. Frontend lê direto, **não recalcula** via delta math.
>
> Exception em **redação manual**: usa **find-and-replace-all** com `expected_text` como fonte de verdade — não confiar só em offsets que podem estar stale.

## Quebra de linha em regex de nome

> [!warning] Use `[ \t]+` não `\s+`
> No regex de nomes BR, separadores são `[ \t]+` (não `\s+`). `\s+` casa newline e vaza nomes para a linha seguinte. Vê `_NAME_BODY` em `augmentations.py`.

## Reprocesso vs delete-and-reupload

> [!info] Duas formas de re-processar
> 1. **Reprocess** (`POST /jobs/{id}/reprocess`) — em-place. Mantém o original em quarentena, apaga artefatos, re-roda com config atual. Vê [[10 - Configurações]].
> 2. **Delete + reupload** — antiga forma. Use quando precisar trocar o **arquivo original** também.

## Git

> [!warning] Nunca commits sem pedido explícito
> Política do harness: agentes de IA não fazem commit a menos que o usuário peça. Aplica a todo agente trabalhando no repo (Claude, Cursor, etc).

## Adicionando uma feature nova

Checklist:

1. Mexeu no schema do banco? Adicionar `_ensure_column` em `db/database.py`.
2. Mexeu no Settings? Atualizar `Settings` class em `config.py`.
3. Mexeu nos schemas Pydantic? Atualizar `types.ts` no frontend.
4. Mexeu em endpoints? Adicionar/atualizar testes em `test_api.py` ou arquivo de feature.
5. Pode logar PII? Adicionar caso a `test_log_privacy.py`.
6. Mexeu na UI? `next build` valida tipos.
7. **Rodar `pytest -q` + `next build`** antes de commitar.

## Métricas (snapshot ~30k LOC)

| Categoria | LOC |
|---|---:|
| Python core (`src/anonymizer/`) | ~3.6k |
| Python API (`src/anonymizer_api/`) | ~6.5k |
| Python testes | ~9.2k |
| Python scripts | ~350 |
| **Total Python** | **~19.5k** |
| TypeScript / TSX | ~7.6k |
| CSS | ~1.6k |
| Markdown (docs) | ~900 |
| Shell + config | ~600 |
| YAML (políticas) | ~160 |
| **Grand total** | **~30k** |

626 testes passando.

## Documentação adicional

- [[00 - Início|Wiki: índice]]
- `CLAUDE.md` — etiqueta para agentes de IA no repo.
- `docs/project-context.md` — fonte de verdade detalhada das convenções.
- `docs/pipeline.md` — fluxo do pipeline.
- `docs/local_setup.md` — setup detalhado.

## Glossário

[[Glossário]] — termos técnicos do projeto (PII, span, marker, lease, etc).
