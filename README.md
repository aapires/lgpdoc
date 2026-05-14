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

#### Caminho recomendado: `install.ps1`

Há um script PowerShell em `scripts/install.ps1` que automatiza praticamente tudo: instala Python 3.11, Node 20 e Git via **winget** (gerenciador de pacotes nativo do Windows 10/11), clona o repo, monta o venv e instala dependências de backend e frontend.

**Pré-requisito**: `winget` precisa estar disponível. Já vem nativo no Windows 11 e na maioria das instalações recentes do Windows 10. Se faltar, instale "App Installer" pela [Microsoft Store](https://apps.microsoft.com/detail/9NBLGGH4NNS1).

Abra o **PowerShell** (não precisa de admin) na pasta onde quer guardar o projeto e rode:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
Invoke-WebRequest https://raw.githubusercontent.com/aapires/lgpdoc/main/scripts/install.ps1 -OutFile install.ps1
.\install.ps1
```

A primeira linha libera execução de scripts apenas para essa sessão de PowerShell (não afeta o resto do sistema). A terceira roda o instalador — leva ~15 min na primeira vez, baixa ~500 MB.

**Flags úteis**:

```powershell
.\install.ps1 -WithOcr               # instala tambem Tesseract OCR (para PDFs escaneados)
.\install.ps1 -WithOpf               # instala torch + transformers + modelo OPF (~2 GB extras)
.\install.ps1 -WithOpf -WithOcr      # tudo
.\install.ps1 -RepoPath D:\projetos  # destino customizado (default: %USERPROFILE%\lgpdoc)
.\install.ps1 -SkipClone             # rode de dentro de uma pasta ja clonada
```

Ao final, o script imprime os dois comandos para subir API+UI em dois terminais. Os comandos básicos (sem OPF, modo regex):

```powershell
# Terminal 1 - backend
$env:ANONYMIZER_API_USE_MOCK_CLIENT = "true"
.\.venv\Scripts\python -m uvicorn scripts.run_api:app --host 127.0.0.1 --port 9000

# Terminal 2 - frontend
cd apps\reviewer-ui
npm run dev
```

Depois abra `http://localhost:3000/jobs`. Para encerrar, `Ctrl+C` nos dois terminais.

> ⚠️ **Microsoft Store stub do Python**: se o `install.ps1` falhar com mensagem sobre stub do Python, vá em **Configurações → Aplicativos → Configurações avançadas de aplicativo → Aliases de execução de aplicativo** e desative `python.exe` e `python3.exe`. Reabra o PowerShell e rode `.\install.ps1 -SkipClone`.

#### Setup manual (sem o script)

Se preferir instalar à mão ou se `winget` não estiver disponível:

1. Baixe e instale **Python 3.11/3.12** ([python.org](https://www.python.org/downloads/windows/) — **marque "Add python.exe to PATH"**), **Node.js 20 LTS** ([nodejs.org](https://nodejs.org/)) e **Git for Windows** ([git-scm.com](https://git-scm.com/download/win)).
2. Em PowerShell, da pasta onde quer guardar o projeto:

```powershell
git clone https://github.com/aapires/lgpdoc.git
cd lgpdoc

python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[dev,api,ocr]"      # regex apenas
# ou:
.\.venv\Scripts\pip install -e ".[dev,api,ocr,ml]"   # com OPF (~2 GB)

cd apps\reviewer-ui
npm install
cd ..\..
```

3. Suba API+UI com os mesmos dois comandos da seção anterior.

#### OCR no Windows — passo manual restante

Mesmo com `install.ps1 -WithOcr`, o **Poppler** (binários para extrair imagens de PDF) não tem pacote winget oficial e precisa ser baixado à mão:

1. Baixe o release mais recente em [github.com/oschwartz10612/poppler-windows/releases](https://github.com/oschwartz10612/poppler-windows/releases).
2. Descompacte em `C:\poppler` (ou outro caminho).
3. Adicione `C:\poppler\Library\bin` ao PATH do sistema.

Se você não usou `-WithOcr`, instale o **Tesseract** pelo [instalador UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) e marque "Portuguese" durante a instalação.

Confirme com `tesseract --version` e `pdftoppm -v` num PowerShell novo.

#### Alternativa: `start-anom.sh` no Git Bash

Se preferir um comando único, abra o **Git Bash** (instalado junto com o Git for Windows) na pasta `lgpdoc` e rode:

```bash
./start-anom.sh --mock      # modo regex
./start-anom.sh             # com OPF (precisa do extra [ml] instalado)
```

Se algum comando reclamar de fim de linha (`CRLF`), rode `git config --global core.autocrlf input` e clone o repositório de novo.

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
scripts/                CLIs + worker do subprocesso OPF + install.ps1 (Windows)
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
- **Windows**: o `bootstrap.sh` não cobre Windows — use o `scripts/install.ps1` (winget + venv + npm), descrito na seção [Windows 10 / 11](#windows-10--11). O `start-anom.sh` ainda funciona via Git Bash; alternativamente suba API e UI em dois terminais PowerShell.
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
