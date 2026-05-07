---
tags: [lgpdoc, comparação, diagnóstico]
---

# Modo de comparação de detectores

Modo **diagnóstico**, não de produção. Roda OPF e regex separadamente sobre o mesmo texto e produz um relatório lado-a-lado mostrando onde cada detector contribuiu.

## Para que serve

- Calibrar política de detecção: "esse tipo de PII OPF está pegando ou só regex?"
- Auditar contribuição de cada lado em um documento real.
- Antes de mudar uma regex (afrouxar/apertar), entender o impacto.

## Como acionar

UI: 3ª opção no upload ("Comparação de detectores"). O backend trata isso como upload em modo `anonymization` e dispara `POST /jobs/{id}/detector-comparison` automaticamente após processar, sinalizado pela query string `?autocompare=1`.

API direto: depois de qualquer job concluído (não em `pending`/`processing`), você pode rodar:
```
POST /jobs/{id}/detector-comparison
```

> [!warning] Comparação NÃO é JobMode
> O backend só conhece `anonymization` e `reversible_pseudonymization`. A "3ª opção" no upload é UI-only — sobe como anonymization e dispara o endpoint diagnóstico depois. Vê `tests/test_mode_separation.py`.

## Garantias

`POST /jobs/{id}/detector-comparison` **nunca** modifica:

- `job.status`
- `job.decision`
- `job.risk_level` / `risk_score`
- `redacted.txt`
- `spans.json` (os spans aplicados pela produção)

Idempotente: chamadas repetidas sobrescrevem o JSON salvo em `var/output/<job_id>/detector_comparison.json`.

## Como funciona internamente

```python
# Endpoint: src/anonymizer_api/routers/detector_comparison.py
1. opf_manager.ensure_loaded()         # liga OPF se estiver off
2. leased = opf_manager.acquire()      # lease pra não ser morto pelo watchdog
3. opf_client = CaseNormalizingClient(leased)
4. report = service.run_detector_comparison(
       job_id, opf_client=opf_client, regex_client=app.state.regex_client
   )
5. opf_manager.release(leased)
```

> [!info] Auto-load do OPF
> A comparação **força** o OPF a carregar — comparação sem o lado-modelo é nonsense. Após a chamada, o toggle do header está ON. O usuário pode desligar manualmente, mas é informado pelo badge.

## OPF da comparação ≠ OPF da produção

> [!warning] Regra crítica do design
> O lado OPF da comparação é `CaseNormalizingClient(opf_subprocess)`, **sem** as augmentações regex BR. As augmentações pertencem ao lado `RegexOnlyClient`. Misturar inverte o sinal do diagnóstico — você acaba comparando OPF+regex contra regex sozinho, o que pinta o OPF como "sempre superior".

## Algoritmo de pareamento

`compare_spans()` em `src/anonymizer/detector_comparison.py`:

1. Para cada span OPF, calcula overlap (Jaccard) com cada span regex no mesmo bloco.
2. Pareamento **greedy**: ordena candidatos por overlap descendente; cada span regex consumido por **no máximo um** OPF.
3. Classifica cada item resultante:

| Status | Critério |
|---|---|
| `both` | overlap ≥ 0.90 + mesmo `entity_type` |
| `type_conflict` | overlap ≥ 0.90 + tipos diferentes (ex: OPF=`account_number` × regex=`cpf`) |
| `partial_overlap` | 0.30 ≤ overlap < 0.90 |
| `opf_only` | sem par regex aceitável |
| `regex_only` | sem par OPF aceitável |

## Relatório

Persistido em `var/output/<job_id>/detector_comparison.json`:

```json
{
  "job_id": "...",
  "summary": { "total": 89, "both": 12, "opf_only": 65, ... },
  "by_entity_type": [
    { "entity_type": "private_person", "summary": {...} },
    ...
  ],
  "items": [
    {
      "block_id": "block-0001",
      "status": "both",
      "entity_type": "cpf",
      "opf_span": {...},
      "regex_span": {...},
      "text_preview": "...12 chars...",
      "context_preview": "...±24 chars..."
    },
    ...
  ],
  "blocks": [{"block_id": "block-0001", "text": "..."}, ...]
}
```

> [!warning] Privacidade
> `text_preview` e `context_preview` **só** vão para o JSON salvo (a UI precisa pra mostrar highlights), **nunca para log**. Janelas pequenas (≤60 / ±24 chars). O diretório `var/output/` é tratado como zona segura, mesmo nível da quarentena.

## Logs do módulo

Apenas metadados: `block_id`, contagens, ratios. Nunca `text`, `text_preview` ou `context_preview`. Vê [[13 - Privacidade]].

## UI

`DetectorComparisonPanel` em `apps/reviewer-ui/src/components/`:
- Resumo global (contadores).
- Filtros por status e tipo.
- Tabela com cada item.
- Highlight no texto original mostrando onde cada lado pegou.

> [!warning] UI defensiva contra offsets conflitantes
> Se dois spans no mesmo bloco têm ranges sobrepostos no texto, a UI degrada para texto puro daquele bloco em vez de crashear. Cada bloco é independente — um bloco com conflito não derruba o resto.

Próximo: [[10 - Configurações]].
