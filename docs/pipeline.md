# Document Pipeline — Fluxo e Limitações

## Visão geral

O `DocumentPipeline` orquestra cinco etapas para transformar um documento em
texto redigido sem vazar conteúdo em logs ou artefatos intermediários:

```
Input file
    │
    ▼
[1] Validate       – extensão permitida, tamanho máximo, integridade básica
    │
    ▼
[2] Hash & ID      – SHA-256 do arquivo original + UUID4 como job_id
    │
    ▼
[3] Extract        – extrator específico por formato → lista de DocumentBlocks
    │
    ▼
[4] Detect         – PrivacyFilterClient.detect(block.text) por bloco
    │
    ▼
[5] Redact         – Redactor.redact(block.text, spans) por bloco
    │
    ▼
[6] Assemble       – blocos redigidos reunidos com BLOCK_SEPARATOR ("\n\n")
    │
    ▼
[7] Save           – redacted.txt, spans.json, job_metadata.json
```

---

## Formatos suportados

| Extensão | Extrator          | Granularidade do bloco | Página disponível | Tabelas |
|----------|-------------------|------------------------|-------------------|---------|
| `.txt`   | `TxtExtractor`    | Parágrafo (`\n\n`)     | Não               | Texto plano |
| `.md`    | `TxtExtractor`    | Parágrafo (`\n\n`)     | Não               | Markdown nativo |
| `.rtf`   | `RtfExtractor`    | Parágrafo (`\n\n`)     | Não               | Texto plano |
| `.pdf`   | `PdfExtractor`    | Página (OCR fallback)  | Sim (1-based)     | Texto plano (sem extração estruturada) |
| `.docx`  | `DocxExtractor`   | Parágrafo + tabela     | Não               | Markdown |
| `.xlsx`  | `XlsxExtractor`   | Planilha (1 tabela md) | Sim (sheet index) | Markdown |
| `.png`   | `ImageExtractor`  | Bloco único (OCR)      | Não               | Texto plano |
| `.jpg`   | `ImageExtractor`  | Bloco único (OCR)      | Não               | Texto plano |
| `.jpeg`  | `ImageExtractor`  | Bloco único (OCR)      | Não               | Texto plano |

### Saída em Markdown

Os extratores que enxergam estrutura (`DocxExtractor`, `XlsxExtractor`)
emitem tabelas como Markdown GitHub-flavored:

```markdown
| Nome | CPF |
| --- | --- |
| Joao Silva | 111.444.777-35 |
```

Após a pseudonimização, a tabela continua estruturalmente íntegra —
os marcadores apenas substituem o conteúdo das células:

```markdown
| Nome | CPF |
| --- | --- |
| [PESSOA_0001] | [CPF_0001] |
```

Pipes (`|`) dentro de células são escapados (`\|`); quebras de linha
são achatadas em espaços. O arquivo de saída continua sendo `.txt`
por compatibilidade com o resto do pipeline — qualquer editor de
texto abre normalmente, e LLMs interpretam o markdown nativamente.

---

## Artefatos de saída

### `redacted.txt`
Texto redigido completo. Blocos são separados por `\n\n`. O comprimento pode
diferir do original (estratégias `suppress` removem conteúdo; `pseudonym` pode
ter comprimento diferente).

### `spans.json`
Lista de spans aplicados. Offsets são relativos ao **texto original completo**
(não ao texto redigido), permitindo auditoria retroativa.

```json
[
  {
    "block_id": "block-0000",
    "page": null,
    "doc_start": 42,
    "doc_end": 50,
    "local_start": 42,
    "local_end": 50,
    "entity_type": "private_person",
    "strategy": "pseudonym",
    "replacement": "Alex Jordan"
  }
]
```

Campos:
- `doc_start` / `doc_end` — posição no texto completo concatenado
- `local_start` / `local_end` — posição dentro do bloco (útil para debug)
- `entity_type` — tipo de PII conforme a política
- `strategy` — estratégia aplicada (`replace`, `pseudonym`, `mask`, `suppress`)
- `replacement` — valor substituto (string vazia para `suppress`)

### `job_metadata.json`
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "source_file": "/abs/path/to/contract.docx",
  "file_hash": "sha256hex...",
  "file_size": 12345,
  "format": "docx",
  "block_count": 6,
  "created_at": "2026-04-30T12:00:00+00:00",
  "policy": "policies/default.yaml",
  "stats": {
    "private_person": 2,
    "private_email": 1,
    "private_phone": 1
  }
}
```

---

## Política de rejeição

O pipeline rejeita arquivos nas seguintes situações:

| Condição                            | Exceção                 |
|-------------------------------------|-------------------------|
| Extensão não reconhecida            | `UnsupportedFormatError`|
| Arquivo acima de `--max-bytes`      | `FileTooLargeError`     |
| `.txt`/`.md` com bytes nulos        | `UnsupportedFormatError`|
| PDF corrompido / não parseável      | `UnsupportedFormatError`|
| DOCX corrompido / não parseável     | `UnsupportedFormatError`|
| XLSX corrompido / não parseável     | `UnsupportedFormatError`|

O limite padrão é **10 MiB**. Para ajustar:

```bash
python scripts/anonymize_document.py --input doc.pdf --max-bytes 52428800  # 50 MiB
```

---

## Segurança de logs

Nenhuma das etapas imprime conteúdo do documento em logs. Os loggers emitem
apenas:
- Caminho do arquivo e tamanho em bytes
- `job_id`, `block_id`, número de spans
- Tipo de entidade, estratégia e posição do span

Para habilitar logs de debug (apenas metadados):

```bash
python scripts/anonymize_document.py --input file.docx --verbose
```

---

## Uso rápido

```bash
# Com mock (sem download do modelo)
python scripts/anonymize_document.py \
  --input examples/synthetic_contract.docx \
  --output out/ \
  --mock

# Com modelo real
python scripts/anonymize_document.py \
  --input examples/synthetic_contract.docx \
  --output out/ \
  --device auto \
  --operating-point precision \
  --min-confidence 0.8
```

Artefatos gerados:

```
out/
├── redacted.txt
├── spans.json
└── job_metadata.json
```

---

## Limitações desta sprint

- **OCR opcional via Tesseract.** PDFs escaneados e imagens stand-alone
  (`.png`, `.jpg`, `.jpeg`) passam por OCR quando os extras `[ocr]` estão
  instalados (`pip install -e '.[ocr]'` + `brew install tesseract
  tesseract-lang poppler`). Sem essas deps, scanned PDFs continuam
  produzindo blocos vazios e uploads de imagem são recusados com 400.
  Detalhes em `docs/local_setup.md` §10.
- **DOCX sem número de página.** A API do `python-docx` não expõe paginação;
  o campo `page` nos spans é sempre `null` para DOCX.
- **XLSX flatten.** Células são concatenadas por linha e por sheet; fórmulas são
  substituídas pelo valor calculado (`data_only=True`). Formatação e gráficos
  são ignorados.
- **Sem suporte a `.doc` legado** (formato binário Word 97–2003).
- **Tamanho máximo padrão: 10 MiB** sobre o arquivo como entregue. Para documentos
  com imagens embutidas grandes, considere aumentar `--max-bytes` ou pré-converter
  para texto.

---

## Modo diagnóstico — Comparação de detectores

Fluxo paralelo, **fora** do pipeline normal. Roda apenas sob demanda via
`POST /jobs/{id}/detector-comparison` e nunca altera estado do job.

```
Quarantine file
    │
    ▼
[1] extract_document(path)        – mesma extração de blocos, helper público
    │
    ▼
[2] OPF (CaseNormalizingClient)   – detecção crua, lado modelo
[2] RegexOnlyClient               – detecção crua, lado regex
    │
    ▼
[3] compare_spans(opf, regex, …)  – Jaccard pareamento + classificação
    │
    ▼
[4] build_comparison_report(…)    – summary global + por tipo + items
    │
    ▼
[5] var/output/<job_id>/detector_comparison.json
```

Pontos chave:

- **Não toca em `redacted.txt`, `spans.json`, `verification_report.json`** —
  artefatos de produção ficam exatamente como estavam.
- **Não muda `status`** do job. Idempotente: rodar 2x apenas sobrescreve o JSON.
- **Lado OPF inclui `CaseNormalizingClient`** (mesmo wrapper da produção) para
  o diagnóstico refletir o que o modelo realmente contribui no pipeline real.
  As augmentações regex (`br_*`) ficam todas no `RegexOnlyClient`.
- **Status possíveis por item**: `both` (≥0.90 + mesmo tipo), `type_conflict`
  (≥0.90 + tipo diferente — costuma ser CPF/CNPJ vs `account_number`),
  `partial_overlap` (0.30–0.90), `opf_only`, `regex_only`.
- **Privacidade**: `text_preview` e `context_preview` são persistidos no
  artefato (a UI precisa pra mostrar highlights), mas **nunca** vão pra log.
