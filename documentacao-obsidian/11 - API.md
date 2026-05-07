---
tags: [lgpdoc, api, http, rest]
---

# API

Documentação dos endpoints HTTP. Para a forma definitiva, abra `/docs` (Swagger) ou `/redoc` no servidor rodando.

Base: `http://localhost:9000` (porta padrão do `start-anom.sh`).

## Health

| Método | Rota | Resposta |
|---|---|---|
| `GET` | `/health` | `{"status": "ok"}` |

## Jobs (documentos avulsos)

| Método | Rota | Função |
|---|---|---|
| `POST` | `/jobs/upload` | Upload multipart. `mode=anonymization` ou `reversible_pseudonymization`. 202 + `{job_id, status, created_at}`. |
| `GET` | `/jobs` | Lista. Query `status=` filtra. |
| `GET` | `/jobs/{id}` | Detalhe — campos do `JobModel` incluindo `opf_used`. |
| `GET` | `/jobs/{id}/report` | `report.json` + `redacted_text` + `applied_spans`. |
| `GET` | `/jobs/{id}/download` | Texto redigido (gated em status `auto_approved` / `approved`). |
| `POST` | `/jobs/{id}/approve` | Marca como aprovado, libera download. Aceita `note`. |
| `POST` | `/jobs/{id}/reject` | Marca como rejeitado. |
| `POST` | `/jobs/{id}/unapprove` | Volta para `awaiting_review`. |
| `POST` | `/jobs/{id}/reprocess` | Re-roda pipeline com configurações atuais. 409 se em pending/processing. Vê [[10 - Configurações]]. |
| `DELETE` | `/jobs/{id}` | Apaga DB rows + arquivos. 409 se em pending/processing. |
| `POST` | `/jobs/{id}/manual-redaction` | Anonimização manual de um trecho selecionado. Vê seção abaixo. |
| `POST` | `/jobs/{id}/spans/{idx}/revert` | Marca span como falso-positivo, desfaz. |
| `POST` | `/jobs/{id}/review-events` | Persiste evento (accept/edit/comment/missed_pii). |
| `GET` | `/jobs/{id}/review-events` | Lista de eventos. |

### Reversível (subset de jobs em modo `reversible_pseudonymization`)

| Método | Rota | Função |
|---|---|---|
| `POST` | `/jobs/{id}/reversible/package` | Texto pseudonimizado + lista de placeholders. |
| `POST` | `/jobs/{id}/reversible/validate` | Valida marcadores num texto submetido. |
| `POST` | `/jobs/{id}/reversible/restore` | Restaura texto para originais. |
| `GET` | `/jobs/{id}/reversible/download` | Download do `restored.txt`. |
| `GET` | `/jobs/{id}/reversible/status` | Mode + disponibilidade. |

## OPF runtime toggle

| Método | Rota | Função |
|---|---|---|
| `GET` | `/api/opf/status` | `{available, enabled, loading, error, in_flight_jobs, idle_timeout_seconds, seconds_until_auto_disable}` |
| `POST` | `/api/opf/enable` | Sobe subprocesso. Bloqueia até ready. 409 se `available=false`. |
| `POST` | `/api/opf/disable` | Mata subprocesso, libera memória. |

Detalhes em [[06 - OPF runtime toggle]].

## Comparação de detectores

| Método | Rota | Função |
|---|---|---|
| `POST` | `/jobs/{id}/detector-comparison` | Roda comparação. **Auto-loads OPF.** |
| `GET` | `/jobs/{id}/detector-comparison` | Retorna o relatório salvo. 404 se não rodou. |

Detalhes em [[09 - Modo de comparação]].

## Containers

| Método | Rota | Função |
|---|---|---|
| `POST` | `/api/containers` | Cria container. |
| `GET` | `/api/containers` | Lista. Query `status=archived` para filtrar. |
| `GET` | `/api/containers/{id}` | Detalhe. |
| `PUT` | `/api/containers/{id}` | Atualiza nome/descrição/status. |
| `DELETE` | `/api/containers/{id}` | Apaga (cascade em documents/mapping/spans). |
| `POST` | `/api/containers/{id}/documents/raw` | Upload de doc sensível (entra no pipeline). |
| `POST` | `/api/containers/{id}/documents/pseudonymized` | Upload de doc já-pseudonimizado (só validação). |
| `GET` | `/api/containers/{id}/documents` | Lista de documentos. |
| `GET` | `/api/containers/{id}/documents/{docId}` | Detalhe do doc. |
| `DELETE` | `/api/containers/{id}/documents/{docId}` | Remove doc. |
| `GET` | `/api/containers/{id}/documents/{docId}/download` | Texto pseudonimizado. |
| `GET` | `/api/containers/{id}/documents/{docId}/review-payload` | Payload para review-pseudonymized. |
| `POST` | `/api/containers/{id}/documents/{docId}/manual-redaction` | Anonimização manual. |
| `GET` | `/api/containers/{id}/mapping` | Tabela de marcadores. |
| `GET` | `/api/containers/{id}/mapping/export-sensitive.xlsx` | XLSX **com a coluna `Valor real`**. Sensível. |
| `POST` | `/api/containers/{id}/restore` | Restaura texto pseudonimizado. |

Detalhes em [[08 - Containers]].

## Settings (runtime)

| Método | Rota | Função |
|---|---|---|
| `GET` | `/settings` | Catalogue: detectores habilitados + lista completa disponível. |
| `PUT` | `/settings` | Atualiza `enabled_detectors`. Persiste em `runtime_config.json`. |

Detalhes em [[10 - Configurações]].

## Modelo de erro

Erros de validação ou estado inválido seguem o formato FastAPI padrão:

```json
{ "detail": "Cannot reprocess job in status 'processing'..." }
```

Códigos:

- `400` — entrada inválida.
- `404` — recurso não encontrado.
- `409` — conflito de estado (ex: tentar deletar um job em `processing`).
- `500` — erro inesperado (logged com stack trace, sem PII).

## Manual redaction

Endpoint chave durante revisão. O usuário seleciona um trecho de texto na UI e clica em "Anonimizar":

```http
POST /jobs/{id}/manual-redaction
{
  "start": 1234,            # offset no texto completo (não no bloco)
  "end": 1245,
  "entity_type": "private_person",
  "expected_text": "João Silva"
}
```

> [!warning] expected_text é fonte de verdade
> O endpoint usa **find-and-replace-all** com `expected_text`, não os offsets nominais. Offsets podem estar stale (após edits anteriores). Se `expected_text` não bater no texto atual, retorna 400. Vê `tests/test_api.py::TestManualRedaction`.

## Padrão de status code

- `200` — sucesso normal.
- `201` — criação (rare — upload usa 202).
- `202` — accepted, processamento em background (uploads + reprocess).
- `204` — sucesso sem corpo (deletes).

Próximo: [[12 - Banco de dados]].
