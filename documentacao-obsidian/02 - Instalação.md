---
tags: [lgpdoc, instalação, setup]
---

# Instalação

Duas rotas: o **bootstrap automático** (recomendado em macOS) ou o **setup manual** (Linux ou quando você quer entender cada passo).

## Bootstrap automático (macOS Big Sur ou mais novo)

Uma linha:

```bash
curl -fsSL https://raw.githubusercontent.com/aapires/lgpdoc/main/bootstrap.sh -o /tmp/bootstrap.sh
bash /tmp/bootstrap.sh
```

O script `bootstrap.sh` faz:

1. Instala **Homebrew** (se faltar).
2. Instala via brew: `python@3.11`, `node@20`, `git`, `tesseract`, `tesseract-lang`, `poppler`.
3. Clona o repositório em `~/lgpdoc` (configurável via `--dir`).
4. Cria `.venv` e instala `[dev,api,ocr]`.
5. Roda `npm install` em `apps/reviewer-ui/`.
6. Roda um smoke test (`pytest tests/test_extractors.py`).

> [!warning] Catalina (10.15)
> O `torch` parou de publicar wheels para macOS 10.15. O bootstrap detecta e silenciosamente desabilita `--with-opf`. A aplicação roda só em modo `--mock`. Vê [[06 - OPF runtime toggle]] para entender o trade-off.

> [!info] Tempo da primeira instalação
> Big Sur: ~10 min. Catalina: 30–60 min (várias deps compilam do código-fonte).

## Setup manual (Linux ou alternativo)

```bash
git clone https://github.com/aapires/lgpdoc.git
cd lgpdoc

# Backend
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev,api,ocr]"      # sem [ml] — só regex
.venv/bin/pip install -e ".[dev,api,ocr,ml]"   # com OPF (~3 GB de modelo na 1ª execução)

# OCR system deps (Linux/Debian)
sudo apt install tesseract-ocr tesseract-ocr-por poppler-utils

# Frontend
cd apps/reviewer-ui && npm install && cd ../..
```

## Subir a aplicação

Sempre via `start-anom.sh`:

```bash
./start-anom.sh                 # OPF real + UI em http://localhost:3000
./start-anom.sh --mock          # regex apenas (sem download de modelo)
./start-anom.sh --reset         # limpa ./var/ antes de subir
./start-anom.sh --no-ui         # só backend (porta 9000)
./start-anom.sh --port 8080     # API em porta custom
./start-anom.sh --no-ocr-setup  # pula instalação automática de OCR
```

`Ctrl+C` encerra os dois processos limpos.

## Validar a instalação

```bash
.venv/bin/pytest -q                                  # 626 testes passando
cd apps/reviewer-ui && npx --no-install next build   # build TypeScript limpo
```

Os dois precisam estar verdes antes de declarar a instalação OK. Vê [[15 - Testes]].

## Modelo OPF — primeira execução

A primeira vez que você ligar o OPF, o pacote `opf` baixa ~3 GB do Hugging Face para `~/.opf/privacy_filter/`. Subsequentes execuções reusam o cache. Para forçar download manual antecipado:

```bash
.venv/bin/python -m opf redact --device cpu --format json "ping"
```

> [!tip] Sem GPU
> O OPF roda em CPU, mas é lento — ~50–150 tokens/s em Intel Haswell. Para uso individual de baixo volume, dá. Para volume, considere uma máquina mais nova ou rode em modo `--mock`.

## Sobre `var/`

Diretório de estado runtime, não versionado:

```
var/
├── quarantine/           # documentos originais (sensíveis)
├── output/               # redacted.txt, spans.json, report.json por job
├── anonymizer_api.db     # SQLite com jobs, spans, containers, eventos
└── runtime_config.json   # detectores habilitados via /settings
```

Para reset completo: `./start-anom.sh --reset` apaga o `var/` antes de subir.

Próximo: [[03 - Arquitetura]].
