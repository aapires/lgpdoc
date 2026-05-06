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

## Setup rápido (macOS Big Sur ou mais novo)

Uma linha:

```bash
curl -fsSL https://raw.githubusercontent.com/aapires/lgpdoc/main/bootstrap.sh -o /tmp/bootstrap.sh
bash /tmp/bootstrap.sh
```

O `bootstrap.sh` instala Homebrew (se faltar), Python 3.11, Node 20, Tesseract, Poppler, clona o repositório em `~/lgpdoc` e instala todas as dependências. Em macOS Big Sur a primeira execução demora ~10 min; em Catalina pode chegar a 30–60 min porque várias dependências compilam do código-fonte.

Para subir a aplicação:

```bash
cd ~/lgpdoc
./start-anom.sh           # com OPF (modo padrão; baixa o modelo na 1ª vez)
./start-anom.sh --mock    # sem OPF — só regex (Catalina ou máquina sem GPU)
```

Abra `http://localhost:3000/jobs`.

### Setup manual (Linux ou se preferir)

```bash
git clone https://github.com/aapires/lgpdoc.git
cd lgpdoc

# Backend
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev,api,ocr]"      # sem [ml] = só regex
.venv/bin/pip install -e ".[dev,api,ocr,ml]"   # com OPF (~3 GB de modelo na 1ª exec)

# OCR system deps (Linux)
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
