---
tags: [lgpdoc, opf, subprocesso, watchdog]
---

# OPF runtime toggle

## Por que existe

O modelo OPF carrega ~3 GB de pesos na RAM. Em uso individual, ficar com isso residente o tempo todo é desperdício — você pode passar horas só fazendo CPF/CNPJ regex e nem precisar do modelo. Por outro lado, quando precisa, a latência de subir do zero é proibitiva.

A solução: **carregar e descarregar sob demanda**, com botão na UI e watchdog que desliga sozinho.

> [!warning] Por que subprocesso?
> torch + glibc no CPU **não devolvem totalmente os ~3 GB ao SO** mesmo após `del modelo` + `gc.collect()` — o allocador não retorna páginas. Para realmente recuperar a memória, o **processo tem que morrer**. Daí o subprocesso.

## Componentes

### `scripts/opf_worker.py`
Script standalone. Importa `OpenAIPrivacyFilterClient`, carrega o modelo, lê JSON de stdin (`{"action": "detect", "text": "..."}`), responde JSON em stdout (`{"spans": [...]}`). Aceita `--mock` (tests) que troca o modelo real pelo `MockPrivacyFilterClient`.

Eventos:
- `{"event": "loading"}` — emitido no boot
- `{"event": "ready"}` — modelo carregado, aceitando trabalho
- `{"event": "error", "message": "..."}` — falha fatal
- `{"event": "bye"}` — resposta ao shutdown

### `SubprocessOPFClient` (`src/anonymizer/subprocess_opf_client.py`)
Implementa `PrivacyFilterClient`. Métodos:
- `start()` — `subprocess.Popen` + espera `ready` (timeout 180s)
- `detect(text)` — escreve JSON, lê resposta. Lock-protegido (uma chamada por vez)
- `stop()` — `{"action": "shutdown"}` ou `terminate()` se não responder
- `is_running()` — checa `proc.poll()`

### `OPFManager` (`src/anonymizer_api/opf_manager.py`)
Owner da máquina de estado:

```
OFF ──enable()──▶  LOADING ──worker emits "ready"──▶ ON
ON  ──disable()─▶  OFF (subprocesso morre, SO recupera os 3 GB)
*   ──disable()─▶  OFF (idempotente)
```

Métodos públicos:
- `enable()` — sobe subprocesso, bloqueia até ready. Idempotente.
- `disable(wait_for_jobs=True)` — espera leases acabarem, depois mata.
- `acquire()` → `PrivacyFilterClient` — toma lease (refcount++). Retorna o subprocesso (ou o fallback se off).
- `release(leased)` — refcount--.
- `ensure_loaded()` — atalho usado pela [[09 - Modo de comparação|comparação]].
- `current_base()` — sem refcount, para `detect()` interativo.
- `touch()` — reseta o relógio do watchdog. Chamado pelo `ToggledBaseClient` em todo `detect()`.
- `shutdown()` — para o watchdog + mata o subprocesso. Chamado no lifespan do FastAPI.

### `ToggledBaseClient` (mesmo arquivo)
Wrapper minúsculo que rotea `detect()`:
```python
def detect(self, text):
    target = self._manager.current_base()
    if isinstance(target, SubprocessOPFClient):
        self._manager.touch()
    return target.detect(text)
```

Bound uma vez no boot do FastAPI, injetado como `base` em `make_augmented_client(...)`. As [[05 - Detectores|augmentações BR]] ficam por cima — não dependem do toggle.

## Lease vs current_base

Duas formas de pegar o cliente, cada uma com semântica diferente:

| Mecanismo | Quando usar | Trade-off |
|---|---|---|
| `acquire()` / `release()` | `JobService.process()` — runs longos com múltiplos `detect()`. | Refcount mantém o subprocesso vivo mesmo se o usuário clicar disable mid-job. |
| `current_base().detect(...)` | Detect ad-hoc (validação de container, residual PII). | Sem refcount — disable concorrente pode levar o próximo `detect()` para o fallback. Aceitável para chamadas curtas. |

> [!warning] Snapshot por job
> O `process()` captura via `acquire()` no início do processamento. Toggle desligado mid-job **não** afeta esse job — ele termina com OPF. Garantia exigida pela coerência do `opf_used` e do estado salvo. Vê [[15 - Testes|test_opf_manager.py]].

## Watchdog idle

Thread daemon dentro do `OPFManager`:

- Roda a cada `min(30, max(5, timeout/6))` segundos.
- Verifica: `enabled AND refcount==0 AND elapsed_since_last_used >= timeout`.
- Se sim, chama `disable(wait_for_jobs=False)`.
- `_last_used_at` é tocado em `enable()`, `acquire()`, `release()` e `touch()` (toda vez que um `detect()` vai pro subprocess).

Configuração em `Settings.opf_idle_timeout_seconds` (default `300` = 5 min). `0` desabilita o watchdog.

> [!info] Por que essas heurísticas
> 5 min cobre o cenário típico ("liguei, processei, esqueci"). Watchdog poll de 1/6 do timeout dá granularidade suficiente sem CPU desperdiçado. `wait_for_jobs=False` no auto-disable: o watchdog só aciona quando refcount==0 anyway.

## Tracking — `opf_used`

O `JobModel` tem coluna `opf_used: bool | None`. Capturada em `JobService.process()`:

```python
leased_base = self.opf_manager.acquire()
opf_used = isinstance(leased_base, SubprocessOPFClient)
self.jobs.update(job_id, opf_used=opf_used)
```

A UI mostra esse valor no [[14 - Frontend|`OpfModeBadge`]] dos cards e do hero do documento. `None` = legado pré-coluna.

## Endpoints HTTP

| Método | Rota | Função |
|---|---|---|
| `GET` | `/api/opf/status` | Retorna `{available, enabled, loading, error, in_flight_jobs, idle_timeout_seconds, seconds_until_auto_disable}` |
| `POST` | `/api/opf/enable` | Bloqueia até ready. 409 se `available=false` (`--mock` mode). |
| `POST` | `/api/opf/disable` | Idempotente. Espera leases. |

Próximo: [[07 - Modos de processamento]].
