---
tags: [lgpdoc, modos, anonymization, reversible]
---

# Modos de processamento

Decisão tomada no upload. Define se o documento processado pode ser **restaurado** ao original ou não.

## Anonimização (default)

PII vira rótulo genérico. Sem caminho de volta.

```
"Cliente: João Silva, CPF 123.456.789-09"
                ↓ anonimização
"Cliente: [PESSOA], CPF [CPF]"
```

- Use quando o documento sai pra uso externo definitivo (envio a terceiro, publicação, arquivamento).
- Aprovou na revisão? Baixa o documento limpo.
- Não há tabela de mapeamento marcador→original.

## Pseudonimização reversível

PII vira marcador indexado. A tabela `(marker, original)` fica salva nos spans.

```
"Cliente: João Silva, CPF 123.456.789-09"
                ↓ pseudonimização reversível
"Cliente: [PESSOA_0001], CPF [CPF_0001]"
```

- Use quando o documento vai ser **processado e depois reidentificado**:
  1. Pseudonimiza o original.
  2. Manda o pseudonimizado para um LLM (resumo, classificação, análise).
  3. Recebe o resultado do LLM (ainda com marcadores).
  4. Restaura o resultado para os valores originais.
- A indexação é case + whitespace insensitive: `Maria Silva` ≡ `MARIA SILVA` ≡ `maria  silva` → mesmo `[PESSOA_0001]`.

## Quando escolher um e não o outro

> [!tip] Regra de ouro
> Se você precisa do dado **estruturado** depois (saber que `[PESSOA_0001]` no resumo é a mesma `[PESSOA_0001]` no original), use reversível. Se é fluxo "vai e não volta", use anonimização — é mais simples e não precisa cuidar da tabela.

| Caso de uso | Modo |
|---|---|
| Compartilhar peça processual com perito externo | Anonimização |
| Pedir ao GPT-4 para resumir um contrato | Reversível |
| Treinar embeddings em corpus interno | Anonimização |
| Análise temática de petições — quero saber qual juiz julgou cada uma | Reversível |
| Backup do que sobrou de log | Anonimização |

## Mesma engine, comportamento diferente

> [!info] Mesmo pipeline
> A diferença entre os dois modos é só **a estratégia da política** (e os endpoints reversíveis estarem disponíveis para um e bloqueados para o outro). Extração, detecção, redação e verificação rodam idênticas. Não há lógica duplicada.

A política `policies/default.yaml` define a estratégia padrão para cada `entity_type`. Quando o modo é `reversible_pseudonymization`, o pipeline força a estratégia para `indexed` (que produz `[PESSOA_NN]`).

## Endpoints reversíveis

Disponíveis só para jobs em modo `reversible_pseudonymization`. Recusam com 400 quando o modo é `anonymization`.

| Endpoint | Função |
|---|---|
| `POST /jobs/{id}/reversible/package` | Retorna o texto pseudonimizado + lista de placeholders. |
| `POST /jobs/{id}/reversible/validate` | Recebe texto pseudonimizado (possivelmente editado por LLM) e valida marcadores. |
| `POST /jobs/{id}/reversible/restore` | Recebe texto pseudonimizado e devolve com originais. |
| `GET /jobs/{id}/reversible/download` | Download do `restored.txt` (gerado pelo restore). |
| `GET /jobs/{id}/reversible/status` | Mode + disponibilidade. |

## Validação no restore

`validate` checa três coisas:

1. **Marcadores ausentes** (no texto submetido aparecem menos vezes que no original).
2. **Duplicados** (mais vezes que o esperado).
3. **Inesperados** (padrões `[XXX_NN]` que não vieram do original — sintoma típico: o LLM "inventou" um marcador).

> [!warning] False positives são pulados no reversível
> Spans marcados como `false_positive` na revisão **não** entram no pacote reversível — o original já está no texto naquela posição, não há marcador para restaurar.

## Single-doc vs Container

Os dois modos acima são para upload **avulso** (`/jobs/upload`). Para múltiplos documentos relacionados, há um modelo paralelo: [[08 - Containers]]. Containers usam pseudonimização compartilhada com tabela de marcadores única para o caso inteiro.

## "Preparar para LLM" — termo proibido

Tanto a UI quanto a API evitam essa expressão. Os dois modos podem alimentar um LLM; o que muda é a **reversibilidade**. Use sempre "Restaurar dados originais", nunca "rehydrate" ou "preparar".

Próximo: [[08 - Containers]].
