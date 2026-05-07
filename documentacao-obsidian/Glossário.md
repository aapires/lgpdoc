---
tags: [lgpdoc, glossário, referência]
---

# Glossário

Termos técnicos usados nesta wiki e no código. Em ordem alfabética.

## Acquire / release
Mecanismo de **lease** do [[06 - OPF runtime toggle|OPFManager]]. `acquire()` toma o cliente atual (subprocess OPF ou fallback) e incrementa um refcount; `release()` decrementa. Enquanto o refcount for > 0, `disable()` espera. Garante que jobs em vôo não tenham o cliente puxado debaixo deles.

## Anonimização
Modo de processamento irreversível. PII vira rótulo genérico (`[PESSOA]`, `[EMAIL]`). Sem caminho de volta. Vê [[07 - Modos de processamento]].

## awaiting_review
Status de um job que terminou o pipeline e precisa de revisão humana antes de poder ser baixado. **Todo job processado vai para esse estado**, mesmo de risco baixo. `decision` e `risk_level` são apenas sinais visuais, nunca pulam revisão.

## Aux detector
Detector regex auxiliar que roda **em paralelo** ao primary client no `CompositeClient`. Lista em `make_augmented_client`: `detect_br_labeled_names`, `detect_cpfs`, `detect_cnpjs`, `detect_endereco_*`, e os de `REGEX_DETECTORS`.

## Block / DocumentBlock
Unidade de extração. Um documento é decomposto em uma `list[DocumentBlock]` (`block_id`, `text`, `page`, `start_offset`, `end_offset`). A detecção e redação rodam por bloco; a saída é remontada com `BLOCK_SEPARATOR = "\n\n"`. Vê [[04 - Pipeline de detecção]].

## CaseNormalizingClient
Wrapper que faz title-case length-preserving em sequências ALL-CAPS antes de passar para o cliente interno. `GUSTAVO SOARES` → `Gustavo Soares` (mas `RJ`/`UF` permanecem). Crucial para o OPF reconhecer nomes BR em caps. Vê `src/anonymizer/augmentations.py`.

## Container
Espaço de trabalho que agrupa múltiplos documentos do mesmo caso/processo. Compartilha tabela de marcadores. Vê [[08 - Containers]].

## decision
Recomendação algorítmica do pipeline: `auto_approve`, `sample_review`, `manual_review`. **Não bypassa** o gate de revisão humana — só sinaliza prioridade.

## DetectedSpan
Dataclass `frozen` em `src/anonymizer/models.py` com (`start`, `end`, `entity_type`, `confidence`, `text_hash`, `source`). Saída dos detectores; entrada do redactor. Offsets são relativos ao **bloco**.

## Detector comparison
Modo diagnóstico que roda OPF e regex separadamente para mostrar contribuição de cada lado. **Não modifica o job**. Vê [[09 - Modo de comparação]].

## DV (Dígito Verificador)
Algoritmo de validação de CPF/CNPJ. Garante que números sintáticamente parecidos com CPF mas inválidos não sejam falsos positivos. Implementado em `regex_detectors.py` / `augmentations.py`.

## Entity type
Tipo de PII detectada — `private_person`, `private_email`, `cpf`, `cnpj`, `oab`, `cep`, etc. Mapeado para estratégia + label em `policies/default.yaml`.

## Fallback client
[[06 - OPF runtime toggle|Cliente de fallback]] usado quando o toggle OPF está OFF. Em produção é o `RegexFallbackClient` (e-mail só, conservador). Em testes (`opf_use_mock_worker=True`) é o `MockPrivacyFilterClient`.

## indexed (estratégia)
Estratégia de redação default para PII textual. Produz marcadores enumerados como `[PESSOA_01]`, `[EMAIL_02]`. A indexação é case + whitespace insensitive.

## Job
Processamento de **um documento avulso** (não em container). Persistido como `JobModel`. Tem ciclo `pending → processing → awaiting_review → approved/rejected`.

## Lease
Vê **acquire / release**.

## make_augmented_client
Factory canônico em `augmentations.py` que monta a stack completa: `CaseNormalizingClient(base) + aux detectors + REGEX_DETECTORS + filtro de kinds`. Nunca instanciar `_OverridingComposite` diretamente.

## Mapping entry
Linha da tabela de marcadores de um [[08 - Containers|container]] — `(container_id, entity_type, marker, original_text, normalized_value)`. Fonte de verdade da pseudonimização do container.

## Marker
Substituição indexada de PII — `[PESSOA_0001]`, `[CPF_0002]`. Em modo single-doc reversível usa 2 dígitos; em container usa 4.

## MockPrivacyFilterClient
Cliente regex heurística — pega "Capitalized Name patterns", e-mail simples, telefone genérico. Para testes. **NÃO** usar em produção (vê histórico do `RegexFallbackClient`).

## OPF (OpenAI Privacy Filter)
Modelo open-source de detecção de PII. ~3 GB de pesos. Carregado em subprocesso isolado para permitir liberação total da memória. Vê [[06 - OPF runtime toggle]].

## opf_used
Coluna no `JobModel` (`bool | None`). Capturada quando o pipeline tomou o lease — `True` se o cliente era um `SubprocessOPFClient`, `False` caso contrário, `None` para jobs legados pré-coluna. Mostrada como [[14 - Frontend|OpfModeBadge]] na UI.

## OPFManager
[[06 - OPF runtime toggle|Owner]] do subprocesso OPF. Lifecycle (enable/disable), refcount (lease), watchdog idle. Vive em `app.state.opf_manager`.

## Policy / política
`policies/default.yaml`. Define `entity_type → strategy + label`. Carregada via `Policy.from_yaml(path)` no início de cada run do pipeline (não cacheada — edits valem para uploads novos).

## PrivacyFilterClient
ABC em `src/anonymizer/client.py`. Interface única do detector. Implementações: `MockPrivacyFilterClient`, `OpenAIPrivacyFilterClient`, `CaseNormalizingClient`, `CompositeClient`, `RegexOnlyClient`, `RegexFallbackClient`, `SubprocessOPFClient`, `ToggledBaseClient`.

## Pseudonimização reversível
Modo de processamento que produz marcadores indexados com tabela de conversão guardada. Permite restaurar o original. Vê [[07 - Modos de processamento]].

## Quarentena
`var/quarantine/<job_id>.<ext>` — arquivo original. Acesso só pela API.

## Reprocess
`POST /jobs/{id}/reprocess`. Apaga artefatos antigos e re-roda o pipeline mantendo o arquivo original. Pega configurações **atuais** (settings, OPF state). Vê [[10 - Configurações]].

## RegexFallbackClient
Cliente de fallback em produção — só detecta e-mail. Substituiu o `MockPrivacyFilterClient` que produzia falsos positivos massivos em ALL-CAPS. Vê [[05 - Detectores]].

## RegexOnlyClient
"Lado regex" do [[09 - Modo de comparação|comparison]]. Roda todos os detectores BR (REGEX_DETECTORS + auxiliares). Independe do OPF.

## Residual PII
Span detectado pelo `Verifier` no **redacted_text** — sinal de que o redactor deixou algo passar. Aparece no `report.json` e na UI de revisão.

## Risk level / risk_score
Sinais calculados pelo `Verifier`. `risk_level` ∈ {low, medium, high, critical}. `risk_score` é numérico. Influenciam o `decision` e a UI mas **não bypassam** revisão humana.

## SettingsStore
[[10 - Configurações|Cache]] thread-safe sobre `runtime_config.json`. Mutável via `PUT /settings`. Filtra spans por `entity_type` habilitado.

## Snapshot (per-job)
Captura do cliente em `JobService.process()` no momento do `acquire()`. Toggle OFF mid-job não afeta o job em vôo — ele continua usando o subprocesso até completar.

## SubprocessOPFClient
[[06 - OPF runtime toggle|Cliente]] que roteia `detect()` para `scripts/opf_worker.py` via stdin/stdout JSON. Lock-protegido (uma chamada por vez).

## text_hash
SHA-256 de PII case-folded e whitespace-collapsed. Permite audit/dedupe sem persistir plaintext. Vê [[13 - Privacidade]].

## ToggledBaseClient
Wrapper minúsculo que `detect()` rotea para `current_base()` do `OPFManager` — subprocesso ou fallback. Bind único no boot do FastAPI.

## Verifier
Fase final do pipeline. Re-roda detectores sobre o `redacted_text` para encontrar PII residual. Vê [[04 - Pipeline de detecção]].

## Watchdog (idle)
Thread daemon do `OPFManager` que mata o subprocesso após N segundos sem atividade. Default 5 min. Configurável via `Settings.opf_idle_timeout_seconds`. Vê [[06 - OPF runtime toggle]].

---

Voltar para [[00 - Início]].
