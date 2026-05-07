---
tags: [lgpdoc, pipeline, core]
---

# Pipeline de detecção

`DocumentPipeline` em `src/anonymizer/pipeline.py` orquestra cinco estágios. É o coração do core. Cada job passa exatamente uma vez por aqui (até o reprocess que cria uma nova rodada).

## Fluxo

```
[1] Validate     extensão, tamanho, integridade básica
       ↓
[2] Hash & ID    SHA-256 do arquivo + UUID4 como job_id
       ↓
[3] Extract      formato → list[DocumentBlock]
       ↓
[4] Detect       client.detect(block.text) por bloco
       ↓
[5] Redact       Redactor.redact(text, spans) por bloco
       ↓
[6] Assemble     blocos redigidos juntos com BLOCK_SEPARATOR ("\n\n")
       ↓
[7] Verify       segunda passada — busca PII residual
       ↓
[8] Save         redacted.txt, spans.json, report.json, job_metadata.json
```

## Formatos suportados

| Extensão | Extrator | Granularidade do bloco | Página? | Tabelas |
|---|---|---|---|---|
| `.txt` | `TxtExtractor` | Parágrafo (`\n\n`) | Não | Texto plano |
| `.md` | `TxtExtractor` | Parágrafo | Não | Markdown nativo |
| `.rtf` | `RtfExtractor` | Parágrafo | Não | Texto plano |
| `.pdf` | `PdfExtractor` | Página | Sim (1-based) | Texto plano (sem extração estrutural) |
| `.docx` | `DocxExtractor` | Parágrafo + tabela | Não | Markdown |
| `.xlsx` | `XlsxExtractor` | Planilha (1 markdown) | Sim (sheet idx) | Markdown |
| `.xls` | `XlsExtractor` | Planilha | Sim | Markdown |
| `.png/.jpg/.jpeg` | `ImageExtractor` | Bloco único | Não | OCR (Tesseract) |

> [!info] Saída em Markdown
> `DocxExtractor` e `XlsxExtractor`/`XlsExtractor` emitem tabelas como Markdown GitHub-flavored. Após pseudonimização, marcadores substituem células sem desestruturar a tabela. LLMs interpretam markdown nativamente.

## Detect — quem chama o quê

O `client` que o pipeline recebe é resultado de `make_augmented_client(base, ...)`:

```
make_augmented_client(base)
  └→ _OverridingComposite (filtra por kinds habilitados)
       ├→ primary: CaseNormalizingClient(base)         # case-fold ALL-CAPS
       └→ aux_detectors:
            • detect_br_labeled_names                   # nomes com label
            • detect_cpfs / detect_cnpjs                # validação DV
            • detect_endereco_logradouro / unidade
            • REGEX_DETECTORS.values()                  # 17 detectores BR
```

`base` é o `[[06 - OPF runtime toggle|ToggledBaseClient]]` em produção. Em modo `--mock`, é o `MockPrivacyFilterClient` (regex heurística — não usado em produção).

> [!warning] Override CPF/CNPJ → account_number
> `_OverridingComposite._override_generic_with_specific()` rebaixa qualquer span de `account_number` que sobreponha um CPF/CNPJ validado. O DV é mais confiável que a classificação do modelo.

## Redact — estratégias

A política em `policies/default.yaml` mapeia `entity_type` → `strategy`:

- **`indexed`** (default) — produz `[NOME_01]`, `[EMAIL_02]`. Indexação é case + whitespace insensitive: `Maria Silva` ≡ `MARIA SILVA` ≡ `maria  silva` → mesmo `[NOME_01]`.
- `replace` — substituição literal pelo `label` da política.
- `pseudonym` — gera valor sintético consistente.
- `mask` — substitui por `*` mantendo comprimento.
- `suppress` — remove o span.

> [!warning] Redactor é stateful
> O contador de marcadores indexados (`_counters`) persiste entre chamadas no mesmo Redactor. Pipeline cria um Redactor novo por job. **Nunca reuse instâncias entre jobs.**

## Verify — segunda passada

Depois da redação, `Verifier` em `src/anonymizer/verification.py` roda novamente os detectores sobre o `redacted_text`. Se algum span PII sobrevive, marca como **PII residual** no relatório.

Score de risco (`risk_score`) e `risk_level` (low/medium/high/critical) saem dessa etapa e ficam no `report.json`.

## Artefatos persistidos

```
var/output/<job_id>/
├── redacted.txt              # texto final redigido
├── spans.json                # offsets relativos ao texto completo
├── report.json               # risk_assessment + residual_spans + rule_findings
├── job_metadata.json         # job_id, hash, política, contagens
└── detector_comparison.json  # opcional, só se rodou comparação
```

## Política de rejeição

Erros que param o pipeline antes de gerar artefatos:

| Condição | Exceção |
|---|---|
| Extensão não reconhecida | `UnsupportedFormatError` |
| Arquivo > `--max-bytes` (default 50 MiB) | `FileTooLargeError` |
| `.txt`/`.md` com bytes nulos | `UnsupportedFormatError` |
| PDF/DOCX/XLSX corrompidos | `UnsupportedFormatError` |

Próximos: [[05 - Detectores]] e [[06 - OPF runtime toggle]].
