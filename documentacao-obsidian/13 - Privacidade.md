---
tags: [lgpdoc, privacidade, segurança, logs]
---

# Privacidade — garantias de design

Esta nota lista as proteções **estruturais** que o projeto adota. Cada item é testado ou verificável por inspeção.

## Logs

> [!warning] NUNCA logar conteúdo de documento, fragmentos PII ou substituições
> Apenas metadados: `job_id`, `block_id`, `span_index`, hashes (SHA-256), posições (start/end), `entity_type`, score, contagens.

Verificável: `grep -nR "logger" src/` deve mostrar só formats com `%s`/`%d` em campos de metadados. Os tests `tests/test_log_privacy.py` rodam todo fluxo com `caplog` e validam que nenhum fragmento PII vazou para os records.

Os flows cobertos:
- Upload + processing + report
- Approve/reject/download
- Revisão manual (manual redaction, revert)
- Reversible package + validate + restore
- Detector comparison
- "All flows combined" (mesmo job percorrendo tudo)

## Quarentena

`var/quarantine/<job_id>.<ext>` armazena o **arquivo original**.

- Acesso só pela API (servidor) — não há endpoint que sirva o arquivo bruto direto.
- Não vai para versionamento (`var/` no `.gitignore`).
- Apaga junto com o job (`DELETE /jobs/{id}` ou `--reset`).

## Saídas (`var/output/<job_id>/`)

- `redacted.txt` — texto redigido. **Pode ser servido** (download) só após `approved`.
- `spans.json` — offsets aplicados. Inclui `original_text` para reversão de falsos-positivos. Tratado como sensível (mesmo nível da quarentena).
- `report.json` — verificação + risco. Inclui `residual_spans` que podem ter `text_preview`. Sensível.
- `detector_comparison.json` — relatório diagnóstico. Inclui `text_preview` e `context_preview`. Sensível.

## Hashing — `text_hash`

Hash determinístico de fragmento PII para audit/dedupe sem persistir plaintext.

```python
def _hash(text: str) -> str:
    normalized = " ".join(text.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

- **Case-folded** + **whitespace-collapsed** → mesmo hash para `Maria Silva` e `MARIA  SILVA`.
- Reuse `_hash_fragment()` ou `augmentations._hash()` — não criar variantes próprias.

## Sem rede em runtime

Nenhum byte de documento sai da máquina:

- Pipeline roda local. OPF é processo filho dentro da mesma máquina.
- Modelo OPF baixado uma única vez do Hugging Face (`~/.opf/privacy_filter/`) na primeira inferência, depois cacheado.
- Nenhum endpoint de telemetria, analytics, crash reporting.

## Subprocesso OPF — isolamento de memória

> [!info] Por que subprocesso, recapitulando
> torch + glibc no CPU não devolvem ~3 GB ao SO mesmo após `del`. Para garantir liberação, o subprocesso tem que morrer. Vê [[06 - OPF runtime toggle]].

Esse design tem efeito secundário positivo: **se o OPF crashar (OOM, segfault no torch), o FastAPI sobrevive** — só o subprocesso morre, o estado da app e dos jobs em vôo é preservado. Próximo `enable()` recria.

## Gates de download

- `/jobs/{id}/download` — só serve em `auto_approved` (legado) ou `approved`. Outros estados → 403.
- `/jobs/{id}/reversible/download` — só serve depois que o reversível restore foi gerado.
- `/api/containers/{id}/documents/{docId}/download` — só em `ready`.

## Container — exportação sensível

A tabela de marcadores de um container contém o pareamento `(marker, original_value)` em texto claro. Exportá-la libera **reidentificação completa** dos documentos pseudonimizados.

> [!warning] Botão com confirmação obrigatória
> A UI mostra um dialog (`window.confirm`) antes de baixar. O endpoint é `/mapping/export-sensitive.xlsx` (nome explícito). Não há export "safe" — foi removido por nunca ter carregado informação útil suficiente.

## Sem PII em testes

Fixtures sintéticas em `tests/conftest.py` + helpers (`_make_cpf`, `_make_cnpj`) que respeitam o algoritmo de DV. Nenhum CPF/CNPJ real, nenhum nome real. Vê [[15 - Testes]].

## Datetimes — UTC explícito

Para evitar interpretação ambígua de timestamp em logs/auditoria, datetimes serializados sempre carregam offset `+00:00`. Vê [[12 - Banco de dados#Datetimes]].

## Quarentena vs output — política unificada

Os dois diretórios (`var/quarantine/` e `var/output/`) são tratados com o mesmo nível de sensibilidade. A operação dos dois precisa estar no mesmo escopo de proteção (volume criptografado, permissões restritas, backup com criptografia, etc).

## O que **não** é protegido

Para eliminar ambiguidade — limites do produto:

- **Tela** — qualquer um com acesso à máquina vê os documentos na UI durante a revisão. Não há autenticação no FastAPI (intenção: deploy single-user, atrás de loopback `127.0.0.1`).
- **Backup do `var/`** — se você copia `var/` para outro lugar, leva tudo: documentos, banco, marcadores. Cuidado.
- **Modelo OPF** — o cache em `~/.opf/privacy_filter/` é só pesos, não memoriza documentos vistos. Mas se você re-criar a árvore de arquivos via snapshot, leva o cache junto.
- **Logs do uvicorn** — emitem só os campos disciplinados pelo logger Python da aplicação. Mas plugins de logging externos (ex: filebeat) podem capturar mais. Cuidado em deployment.

Próximo: [[14 - Frontend]].
