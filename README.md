# LGPDoc

> Pipeline local de anonimização e pseudonimização de documentos brasileiros — OpenAI Privacy Filter + regras determinísticas BR + interface de revisão.

LGPDoc é uma aplicação self-hosted que detecta, anonimiza e pseudonimiza dados pessoais (PII) em documentos. Desenhada para profissionais e times pequenos que precisam preparar documentos para análise externa — advocacia, perícia, processamento por LLMs — sem expor dados sensíveis.

Roda inteiramente local: nenhum byte do documento sai da máquina. A única comunicação externa é o download único do modelo OpenAI Privacy Filter (~3 GB) na primeira execução, opcional.

---

## Características

- **Detecção em duas camadas**:
  - **OpenAI Privacy Filter** (semântico) — pega nomes em narrativa, em ALL-CAPS, em contextos sutis.
  - **30+ detectores regex BR** — CPF/CNPJ com validação de DV, RG, CNH, OAB, CRM, CREA, passaporte, título eleitoral, processo CNJ, datas em PT-BR, endereços, CEP, IP, e tipos de empresa.
- **Dois modos de saída**:
  - **Anonimização** — substituições irreversíveis (`[PESSOA]`, `[EMAIL]`...). Aprovou? Baixa o documento limpo.
  - **Pseudonimização reversível** — marcadores indexados (`[PESSOA_0001]`, `[CPF_0002]`...) com tabela de conversão. Permite o fluxo "original → pseudonimizado → LLM → restaurar para original".
- **Containers** — espaços de trabalho onde múltiplos documentos do mesmo caso compartilham a tabela de marcadores. O mesmo CPF em 5 documentos sempre vira `[CPF_0001]` em todos.
- **OPF toggle runtime** — o modelo carrega/descarrega sob demanda via botão no header. ~3 GB de RAM só quando você precisa. Auto-desliga após 5 min sem uso.
- **Modo diagnóstico** — comparação OPF vs regex pura para ver quem detectou o quê e calibrar políticas.
- **OCR** opcional via Tesseract (PT-BR por padrão) — para PDFs escaneados e imagens.
- **Formatos suportados**: `.txt`, `.md`, `.rtf`, `.pdf`, `.docx`, `.xlsx`, `.xls`, `.png`, `.jpg`, `.jpeg`.
- **Privacidade nos logs** — apenas metadados (`job_id`, `block_id`, hash, posições, contagem). Conteúdo de documento e fragmentos PII nunca aparecem.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Core / detecção | Python 3.11+, regex puro, opcional `opf` + `torch` + `transformers` |
| API | FastAPI ≥0.110, SQLAlchemy 2.0 (SQLite local), Pydantic 2 |
| UI | Next.js 14 App Router, React 18, TypeScript strict, CSS plain |
| OCR | Tesseract (sistema) + pytesseract + pdf2image |
| Persistência | SQLite (`var/anonymizer_api.db`) — sem dependência de banco externo |

---

## Setup

Escolha o caminho conforme seu sistema operacional.

### macOS Big Sur ou mais novo

Uma linha:

```bash
curl -fsSL https://raw.githubusercontent.com/aapires/lgpdoc/main/bootstrap.sh -o /tmp/bootstrap.sh
bash /tmp/bootstrap.sh
```

O `bootstrap.sh` instala Homebrew (se faltar), Python 3.11, Node 20, Tesseract, Poppler, clona o repositório em `~/lgpdoc` e instala todas as dependências. Em Big Sur a primeira execução demora ~10 min; em Catalina pode chegar a 30–60 min porque várias dependências compilam do código-fonte.

Para subir a aplicação:

```bash
cd ~/lgpdoc
./start-anom.sh           # com OPF (modo padrão; baixa o modelo na 1ª vez)
./start-anom.sh --mock    # sem OPF — só regex (Catalina ou máquina sem GPU)
```

Abra `http://localhost:3000/jobs`.

### Windows 10 / 11

Não há instalador automático no Windows — você instala três coisas, clona o repo e sobe em dois terminais. Plano: ~15–20 min na primeira vez. Recomendo começar pelo **modo `--mock`** (só regex, sem download de modelo) para validar que tudo funciona antes de baixar os ~3 GB do OPF.

#### 1. Pré-requisitos (instaladores oficiais)

Baixe e instale, nessa ordem:

1. **Python 3.11 ou 3.12** → [python.org/downloads](https://www.python.org/downloads/windows/)
   - **Marque "Add python.exe to PATH"** na primeira tela do instalador. É a checkbox mais importante.
2. **Node.js 20 LTS** → [nodejs.org](https://nodejs.org/) — escolha o instalador "LTS".
3. **Git for Windows** → [git-scm.com/download/win](https://git-scm.com/download/win) — traz junto o **Git Bash**, que vamos usar para clonar e (opcionalmente) rodar o script de start.

Para confirmar que tudo entrou no PATH, abra um **novo** PowerShell e rode:

```powershell
python --version    # deve mostrar 3.11.x ou 3.12.x
node --version      # deve mostrar v20.x
git --version
```

Se algum comando não for reconhecido, feche e abra o PowerShell de novo. Se ainda assim falhar, o instalador não adicionou ao PATH — reinstale marcando a opção.

#### 2. Clone e setup

No PowerShell, da pasta onde você quer guardar o projeto (ex.: `C:\Users\<seunome>\Projects`):

```powershell
git clone https://github.com/aapires/lgpdoc.git
cd lgpdoc

# Cria o venv
python -m venv .venv

# Atualiza o pip dentro do venv
.\.venv\Scripts\python -m pip install --upgrade pip

# Instala o backend em modo regex (leve — recomendado para o primeiro teste)
.\.venv\Scripts\pip install -e ".[dev,api,ocr]"

# Instala o frontend
cd apps\reviewer-ui
npm install
cd ..\..
```

> 💡 No Windows, o venv vive em `.venv\Scripts\` (e não em `.venv/bin/` como no macOS/Linux). Os comandos abaixo usam o caminho do Windows.

#### 3. Subir a aplicação (sem OPF — modo `--mock`)

Você precisa de **dois terminais PowerShell** abertos em paralelo, ambos na pasta `lgpdoc`.

**Terminal 1 — backend (API FastAPI):**

```powershell
$env:ANONYMIZER_API_USE_MOCK_CLIENT = "true"
.\.venv\Scripts\python -m uvicorn scripts.run_api:app --host 127.0.0.1 --port 9000
```

Espere a mensagem `Uvicorn running on http://127.0.0.1:9000`.

**Terminal 2 — frontend (Next.js):**

```powershell
cd apps\reviewer-ui
npm run dev
```

Espere `ready - started server on 0.0.0.0:3000`. Abra `http://localhost:3000/jobs` no navegador.

Para encerrar, dê `Ctrl+C` nos dois terminais.

#### 4. (Opcional) Ligar o OPF

O OPF é semântico — pega nomes em narrativa sem precisar de gatilhos. Mas baixa ~3 GB na primeira execução e consome ~3 GB de RAM enquanto carregado. Só vale a pena se a máquina tiver pelo menos **8 GB de RAM livres**.

```powershell
.\.venv\Scripts\pip install -e ".[dev,api,ocr,ml]"
```

E suba a API **sem** o `ANONYMIZER_API_USE_MOCK_CLIENT=true` do passo anterior:

```powershell
.\.venv\Scripts\python -m uvicorn scripts.run_api:app --host 127.0.0.1 --port 9000
```

Na UI, o toggle "OPF" no header carrega/descarrega o modelo sob demanda.

#### 5. (Opcional) OCR para PDFs escaneados e imagens

Sem essas duas dependências, PDFs escaneados produzem texto vazio e uploads de imagem (`.png`, `.jpg`) são recusados com erro 400.

- **Tesseract OCR** — instalador UB-Mannheim: [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki).
  - Durante a instalação, em "Additional language data", **marque "Portuguese"**.
  - Adicione `C:\Program Files\Tesseract-OCR` ao PATH.
- **Poppler** (binários do PDF) — baixe o release mais recente em [github.com/oschwartz10612/poppler-windows/releases](https://github.com/oschwartz10612/poppler-windows/releases).
  - Descompacte em `C:\poppler` (ou outro lugar) e adicione `C:\poppler\Library\bin` ao PATH.

Confirme rodando `tesseract --version` e `pdftoppm -v` num novo PowerShell.

#### Alternativa: usar o `start-anom.sh` no Git Bash

Se preferir um único comando, abra o **Git Bash** (instalado junto com o Git for Windows) na pasta `lgpdoc` e rode:

```bash
./start-anom.sh --mock      # modo regex
./start-anom.sh             # com OPF (precisa do extra [ml] instalado)
```

O script foi escrito para macOS/Linux, mas funciona no Git Bash. Se algum comando reclamar de fim de linha (`CRLF`), rode `git config --global core.autocrlf input` e clone o repositório de novo.

### Linux (Ubuntu/Debian) — setup manual

```bash
git clone https://github.com/aapires/lgpdoc.git
cd lgpdoc

# Backend
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev,api,ocr]"      # sem [ml] = só regex
.venv/bin/pip install -e ".[dev,api,ocr,ml]"   # com OPF (~3 GB de modelo na 1ª exec)

# OCR system deps
sudo apt install tesseract-ocr tesseract-ocr-por poppler-utils

# Frontend
cd apps/reviewer-ui && npm install && cd ../..

./start-anom.sh
```

Detalhes adicionais em [`docs/local_setup.md`](docs/local_setup.md).

---

## Como usar

1. **Upload** — arraste um documento (`.pdf`, `.docx`, `.xlsx`, `.txt`, etc.) e escolha o modo: anonimização ou pseudonimização reversível.
2. **Processamento** — o pipeline extrai → detecta → redige → verifica. Status na lista atualiza a cada 1.5 s.
3. **Revisão** — o documento abre na tela de revisão. Aceite, edite ou marque como falso-positivo cada trecho. Selecione texto manualmente para anonimizar trechos que escaparam.
4. **Configurações** — em `/settings` você liga/desliga famílias de detectores. Existem presets prontos (Leve, Intermediário, Pesado, Crítica) que combinam conjuntos diferentes.
5. **Reprocessar** — se mudou o preset ou ligou/desligou OPF e quer re-rodar no mesmo documento sem re-upload, o botão "Reprocessar" na tela de revisão refaz tudo com as configurações ativas no momento.
6. **Aprovar** → download do documento processado.

---

## Conceitos

### Anonimização vs Pseudonimização reversível

Decisão tomada no upload:

- **Anonimização**: PII vira rótulo genérico (`[PESSOA]`, `[EMAIL]`). Sem retorno — escolha quando o documento vai para uso externo definitivo.
- **Pseudonimização reversível**: PII vira marcador indexado (`[PESSOA_0001]`, `[EMAIL_0002]`). Permite o ciclo completo:
  1. Documento original → pseudonimizado.
  2. Texto pseudonimizado vai para um LLM (resumo, análise, classificação).
  3. Resultado do LLM (ainda com marcadores) → endpoint de restore.
  4. Texto restaurado contém os valores originais de volta nos lugares certos.

### OPF runtime toggle

O OpenAI Privacy Filter é semântico (pega "João Silva confirmou o envio" sem precisar de "Cliente:" antes), mas custa ~3 GB de RAM enquanto carregado. O toggle no header da UI permite subir o modelo só quando necessário e o watchdog interno desliga automaticamente após 5 min sem uso. Configurável em `Settings.opf_idle_timeout_seconds` (`0` desabilita o auto-desligamento).

Sem OPF, o pipeline ainda detecta tudo que regex acerta sem ambiguidade: e-mail, CPF, CNPJ (com DV), RG, OAB, telefones brasileiros, datas em PT-BR, endereços, CEP, e nomes precedidos de gatilhos ("Cliente:", "Sr.", "Auditor", "Diretora", etc).

### Containers

Para casos com múltiplos documentos relacionados (processo, contrato, perícia), crie um container. Todos os documentos compartilham a tabela de marcadores e a normalização de valores: o mesmo CPF detectado em 5 documentos sempre vira `[CPF_0001]` em todos. Permite:

- Revisar o caso completo numa única tabela de conversão.
- Exportar a tabela "marcador → valor real" (planilha sensível).
- Re-importar documentos pseudonimizados que foram editados (validação detecta marcadores inesperados ou ausentes).

### Modo diagnóstico

3ª opção no upload: a aplicação roda OPF e o `RegexOnlyClient` separadamente sobre o mesmo texto e gera um relatório lado-a-lado mostrando onde concordaram, onde discordaram, e o que só um detector pegou. Não altera o documento — é só inspeção.

---

## Estrutura do repositório

```
src/anonymizer/         Core — detectores, redator, pipeline, política
src/anonymizer_api/     FastAPI — routers, DB, jobs, containers, OPF manager
apps/reviewer-ui/       Next.js — interface de revisão (PT-BR)
policies/default.yaml   Política de redação (entity_type → estratégia)
scripts/                CLIs + worker do subprocesso OPF
docs/                   project-context.md, pipeline.md, local_setup.md
tests/                  pytest — 626 casos
var/                    Estado runtime (NÃO versionado): quarantine/, output/, db
```

---

## Privacidade — garantias de design

- **Logs**: apenas metadados (`job_id`, `block_id`, `entity_type`, posições, hashes SHA-256). Texto do documento, fragmentos PII e substituições nunca chegam aos logs.
- **Quarentena**: documento original em `var/quarantine/<job_id>.<ext>`, acessível só pela API.
- **Sem rede em runtime**: nenhum byte do documento sai da máquina. O modelo OPF é baixado uma vez do Hugging Face e ca­cheado em `~/.opf/privacy_filter/`.
- **Hashing**: `text_hash` em spans é SHA-256 do conteúdo case-folded e whitespace-collapsed — permite auditar e deduplicar sem persistir plaintext.
- **Subprocesso isolado para OPF**: quando você desliga o toggle, o subprocesso morre e o sistema operacional reclama integralmente os ~3 GB do modelo. Sem leak de memória entre sessões.

---

## Validação

Antes de declarar uma mudança como "feita":

```bash
.venv/bin/pytest -q                                  # 626 testes Python
cd apps/reviewer-ui && npx --no-install next build   # build TypeScript
```

Os dois precisam estar verdes. Não há CI configurado — é responsabilidade local.

---

## Limitações conhecidas

- **macOS Catalina (10.15) ou anterior**: o `torch` parou de publicar wheels para essa versão. O OPF não roda; use `./start-anom.sh --mock` (regex apenas).
- **Windows**: o `bootstrap.sh` não cobre Windows — o setup é manual (instaladores de Python, Node e Git for Windows, depois `pip install` e `npm install`). Veja a seção [Windows 10 / 11](#windows-10--11). O `start-anom.sh` funciona via Git Bash; alternativamente suba API e UI em dois terminais PowerShell.
- **Sem suporte a `.doc` legado** (Word 97-2003 binário) — converta para `.docx` antes.
- **DOCX não expõe número de página** — `page` fica `null` para esse formato.
- **OCR opcional** — sem `[ocr]` instalado, PDFs escaneados produzem blocos vazios e uploads de imagem são recusados com 400.
- **CPU only** — não foi testado com CUDA. Em iMac/MacBook Intel sem GPU, OPF processa ~50–150 tokens/s.

---

## Documentação adicional

- [`docs/project-context.md`](docs/project-context.md) — convenções, regras críticas, padrões arquiteturais (referência para humanos e agentes de IA trabalhando no repo).
- [`docs/pipeline.md`](docs/pipeline.md) — fluxo do `DocumentPipeline`, formatos suportados, política de rejeição.
- [`docs/local_setup.md`](docs/local_setup.md) — setup detalhado do venv, OPF, OCR, smoke tests.
- [`CLAUDE.md`](CLAUDE.md) — etiqueta para agentes de IA (Claude Code) trabalhando no repositório.

---

## Licença

MIT — veja [`LICENSE`](LICENSE).

## Autor

Desenvolvido por **Alexandre A. Pires** — [aapires@gmail.com](mailto:aapires@gmail.com).
