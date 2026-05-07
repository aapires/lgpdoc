---
tags: [lgpdoc, containers, mapping, marker]
---

# Containers

Espaço de trabalho que agrupa múltiplos documentos do mesmo caso/contrato/processo. Garantia central: **o mesmo valor real sempre vira o mesmo marcador, em qualquer documento dentro do container**.

## Por que existe

Imagine uma análise jurídica com 8 documentos sobre o mesmo cliente "Maria Costa, CPF 111.222.333-44".

- Sem container: cada documento é um job independente. `Maria Costa` pode virar `[PESSOA_0001]` num, `[PESSOA_0003]` em outro. Inviável correlacionar.
- Com container: o resolver de marcadores pega o `(container_id, entity_type, normalized_value)` e devolve sempre o mesmo `[PESSOA_0001]`. Os 8 documentos compartilham a tabela.

## Modelo de dados

```
ContainerModel
└── ContainerDocumentModel (n)
     └── ContainerSpanModel (n) ──┐
                                  ├──▶ ContainerMappingEntryModel (n)
ContainerSpanModel ──────────────┘     ↑
                                       │
        UniqueConstraint(container_id, marker)
        UniqueConstraint(container_id, entity_type, normalized_value)
```

- `ContainerMappingEntryModel` é a **fonte de verdade** do par `(marker, original_value)`. Uma linha por valor único dentro do container.
- `ContainerSpanModel` é a ocorrência: marca onde aquele valor apareceu em qual documento.
- `ContainerDocumentModel` é o documento dentro do container — tem um `JobModel` associado para o pipeline de detecção/revisão.

> [!info] Marcadores aqui são `[PESSOA_NNNN]` (4 dígitos)
> Diferente do modo single-doc reversível (`[PESSOA_NN]`, 2 dígitos). A capacidade extra reflete o caso de uso: container pode ter centenas de markers únicos cobrindo dezenas de documentos.

## Tipos de upload

Dois caminhos, decididos no upload:

### `raw_sensitive_document`
Documento original, cheio de PII. Vai pelo pipeline normal:

```
upload → pending → processing → pending_review → (revisão humana) → ready
```

Detecção segue o pipeline padrão ([[04 - Pipeline de detecção]]) com o resolver de marcadores aplicado: cada PII detectada consulta a tabela do container e ou cria uma entrada nova ou reusa o marker existente.

### `already_pseudonymized_document`
Documento que **já vem pseudonimizado** com markers do mesmo container (ex: você gerou um documento via LLM e quer importar de volta). Pula a detecção; só **valida** os marcadores presentes:

- Conhecidos? → ✅ usa a tabela existente.
- Desconhecidos (não estão na tabela)? → ⚠️ destacados na UI; o usuário pode mapear manualmente.
- Malformados (`[NOME_]` sem número)? → ❌ erro.

## Resolver de marcadores

`MarkerResolver` em `src/anonymizer_api/containers/marker_resolver.py`:

```python
resolver.resolve(entity_type="cpf", original="123.456.789-09")
  ├─ normalize → "12345678909"
  ├─ lookup (container_id, "cpf", "12345678909")
  ├─ if found:  return existing.marker
  └─ if not:    INSERT MappingEntry(marker=f"[CPF_{next_index:04d}]", original=...)
                return new.marker
```

A normalização varia por `entity_type`:

| Tipo | Normalização |
|---|---|
| CPF / CNPJ | só dígitos |
| Email | lowercase |
| Telefone | só dígitos, sem prefixo internacional |
| Pessoa | trim + collapse whitespace + case-fold |
| Endereço | trim + collapse whitespace |

Detalhes em `containers/normalizers.py`.

## Tabela de conversão (sensível)

UI em `/containers/[id]/mapping`:

| Marcador | Tipo | Valor normalizado | Ocorrências | Revisão |
|---|---|---|---|---|
| `[PESSOA_0001]` | private_person | maria costa | doc1.docx, doc2.pdf | aprovada |
| `[CPF_0001]` | cpf | 12345678909 | doc1.docx, doc3.txt | aprovada |

> [!warning] Exportação sensível
> O endpoint `/api/containers/{id}/mapping/export-sensitive.xlsx` retorna a tabela **com a coluna `Valor real`** (PII original em texto claro). Botão da UI tem dialog de confirmação. Trate o arquivo gerado como dado sensível — não compartilhe e armazene apenas em ambiente seguro. O safe export foi removido (vê histórico do projeto).

## Restauração no nível de container

`POST /api/containers/{id}/restore` recebe um texto com marcadores (tipicamente saída de um LLM trabalhando sobre os pseudonimizados do container) e devolve o texto restaurado com os valores originais.

Validação faz as mesmas três checagens do reversível single-doc ([[07 - Modos de processamento#Validação no restore]]).

## Isolamento entre containers

> [!warning] Container_id é o único filtro válido
> Markers como `[PESSOA_0001]` em **dois containers diferentes** podem apontar para pessoas diferentes. Toda query em mapping/spans filtra por `container_id` obrigatoriamente. Os testes em `tests/test_container_export.py::TestExportIsolation` blindam contra regressão.

## Lifecycle hooks

`ContainerService` não importa `JobService` (e vice-versa). Os hooks de lifecycle (job processado → marca documento como pending_review; job aprovado → promove documento para ready) ficam em `main.py` e são injetados no `JobService` via callback. Vê [[03 - Arquitetura|inversão de dependências]].

## Telas

| Rota | Função |
|---|---|
| `/containers` | Lista de containers. |
| `/containers/new` | Criar novo. |
| `/containers/[id]` | Detalhe — lista de documentos, upload, ações. |
| `/containers/[id]/mapping` | Tabela de marcadores. |
| `/containers/[id]/restore` | Restaurar texto pseudonimizado. |
| `/containers/[id]/documents/[docId]/review-pseudonymized` | Revisar doc importado já-pseudonimizado. |

Próximo: [[09 - Modo de comparação]].
