---
project_name: 'LGPDoc'
internal_pkg: 'anonymizer'
user_name: 'Alexandre A. Pires'
date: '2026-05-01'
status: 'complete'
sections_completed:
  - technology_stack
  - language_rules
  - framework_rules
  - testing_rules
  - code_quality
  - workflow
  - critical_dont_miss
  - detector_comparison_mode
optimized_for_llm: true
existing_patterns_found: 10
---

# Project Context — LGPDoc

_Produto chamado **LGPDoc**; o pacote Python interno permanece como
`anonymizer` (mantido por compatibilidade de imports e histórico)._

_Regras críticas e padrões que agentes de IA devem seguir ao implementar código neste projeto. Foco em detalhes não óbvios que agentes podem esquecer._

---

## Technology Stack & Versions

### Core
- **Python 3.11+** (venv local em `/Users/aapires/Projects/anonimizador/.venv`, hoje rodando 3.14)
- **Node 20+** com **npm**
- **macOS Darwin** (sandbox local; sem CUDA, OPF roda em CPU)

### Backend — `src/anonymizer/` (core) e `src/anonymizer_api/` (FastAPI)
- `opf` 0.1.0 — OpenAI Privacy Filter (modelo cacheado em `~/.opf/privacy_filter/`, ~2.6 GB)
- `torch` 2.11, `transformers` 5.7
- `fastapi` ≥0.110, `uvicorn` ≥0.27, `python-multipart` ≥0.0.9
- `sqlalchemy` ≥2.0 (modo 2.0 com `Mapped`/`mapped_column`)
- `pydantic` ≥2.0, `pydantic-settings` ≥2.0
- Extratores: `pypdf` ≥4.0, `python-docx` ≥1.1, `openpyxl` ≥3.1
- `pyyaml` ≥6.0
- Build: `setuptools` ≥72 + `wheel`

### Dev / Testes
- `pytest` ≥8.0, `pytest-cov` ≥5.0, `httpx` ≥0.27 (TestClient)

### Frontend — `apps/reviewer-ui/` (Next.js App Router)
- `next` ^14.2.0 (App Router, **NÃO** Pages Router)
- `react` ^18.3.0, `react-dom` ^18.3.0
- `typescript` ^5
- **CSS plain** em `src/app/globals.css` — **sem** Tailwind, Material, shadcn etc.

### Restrições de versão importantes
- Backend instalado em **modo editável** com `setuptools.build_meta`. Hatchling falha com Python 3.14, não voltar.
- `next` 14.2 (App Router); upgrade para 15+ requer revisar `params: { job_id: string }` que muda para `Promise`.
- `transformers` ≥4.50 é exigência do `opf`; abaixo disso o modelo não carrega.

---

## Critical Implementation Rules

### Python — Convenções não óbvias

- **`from __future__ import annotations`** no topo de **todos** os módulos. Permite forward refs e adia avaliação.
- **Type hints PEP 604** sempre: `str | None` (NUNCA `Optional[str]`), `list[X]` (NUNCA `List[X]`), `dict[K, V]` (NUNCA `Dict[K, V]`). Não importar de `typing` o que está builtin.
- **Layout `src/`** com `[tool.setuptools.packages.find] where = ["src"]`. Pacotes acessíveis como `anonymizer` e `anonymizer_api` após `pip install -e .`.
- **Logger por módulo**: `logger = logging.getLogger(__name__)`. NUNCA usar `print` ou `logging.info` direto.
- **Sem comentários de "o que"**: identificadores nomeados explicam o quê. Comentários só para o **porquê** quando não óbvio.
- **Docstrings curtas** (1–3 linhas). Nada de bloco de parâmetros tipo Sphinx — type hints já documentam.
- **Imports pesados são lazy** quando opcionais: `opf`, `torch`, `pypdf` etc. importados dentro do método que precisa, com `try/except ImportError` produzindo `RuntimeError` explicativo. NUNCA importar `torch` no topo de módulos.
- **Dataclasses para value objects**: `@dataclass(frozen=True)` para coisas como `DetectedSpan`, `AppliedSpan`. Mutáveis (`@dataclass`) só para resultados acumulados.
- **f-strings** sempre. NUNCA `%` ou `.format()`.
- **Path > os.path**: usar `pathlib.Path` em todo lugar.
- **`with file.open(...)`** ou `Path.read_text(encoding="utf-8")` — sempre encoding explícito.
- **Sem `Optional[...]`, `Union[A, B]`, `List`, `Dict`, `Tuple`, `Set`** importados de `typing`. Usar sintaxe builtin.
- **Erros internos não tratados sobem**: validar **só** em fronteiras (entrada de usuário, API externa). Não pôr try/except defensivo no meio da chamada.

### TypeScript / React — Convenções não óbvias

- **Next.js App Router** (`src/app/`). NUNCA criar nada em `pages/` (legacy).
- **`"use client"` no topo** de qualquer componente que use `useState`, `useEffect`, `useRef`, event handlers, ou `window.*`. Componentes server-side default.
- **Path alias `@/`** → `./src/`. Imports devem usar `@/lib/...`, `@/components/...`. Nunca relativos `../../../`.
- **TypeScript strict mode**: `strict: true` no `tsconfig.json`. Tipagem explícita em params e retornos públicos. `any` é proibido — use `unknown` se preciso.
- **Tipos em `src/lib/types.ts`**, espelhando os schemas Pydantic do backend. Manter em sync ao alterar API.
- **Cliente API em `src/lib/api.ts`** com `fetchJSON<T>()` helper. Toda chamada à API passa por lá; nada de `fetch()` espalhado em componentes.
- **CSS classes via `className`**. `style={...}` inline só para valores dinâmicos (posição de popover, etc.). Nada de CSS-in-JS, Tailwind, Emotion etc.
- **Componentes funcionais** apenas. NUNCA `class Component extends`.
- **Sem index.ts barrels** desnecessários. Imports diretos.
- **Cleanup em `useEffect`**: retornar função para `clearTimeout`, `removeEventListener` etc. Padrão obrigatório.
- **Modo mock para dev offline**: `NEXT_PUBLIC_USE_MOCKS=true` em `.env.local` faz `api.ts` retornar dados de `src/lib/mocks.ts`. Toda função em `api.ts` precisa de ramo `if (USE_MOCKS) {...}`.
- **`router.push()` para navegação programática**, `<Link>` para navegação declarativa. Importar `useRouter` de `next/navigation` (não `next/router`).

### Framework-Specific Rules

#### FastAPI

- **App factory pattern**: `create_app(settings: Settings | None = None) -> FastAPI`. Único ponto de bootstrap. Tests passam `settings` customizadas.
- **Recursos compartilhados em `app.state`**: `database`, `storage`, `client` (augmentado, produção), `opf_client` (lado OPF da comparação — `CaseNormalizingClient` envolvendo o base), `regex_client` (`RegexOnlyClient`), `service_factory`, `settings_store`, `settings`. NUNCA criar globals em módulo.
- **Dependency injection** via `Depends(...)`. Helpers em `src/anonymizer_api/deps.py`. Acesso a `app.state` via `Request: request.app.state.x`.
- **DB session per request**: gerador `get_db()` cede sessão e fecha no teardown. Background tasks abrem **própria** sessão (a do request já foi fechada).
- **Routers em `routers/`**, registrados em `main.py`. Cada arquivo um router com prefix dedicado (`/jobs`, `/settings`).
- **Schemas Pydantic em `schemas.py`**: classes para request body e response. `ConfigDict(from_attributes=True)` para conversão de ORM.
- **`field_serializer`** para datetimes que precisam saída UTC explícita (SQLite perde tzinfo). Não confiar em serialização default.
- **CORS configurado por settings** (`cors_origins`). Não hard-codar origens.
- **Lifespan** usado via `FastAPI(lifespan=...)` quando precisar startup/shutdown — hoje não usamos, factory faz tudo.

#### SQLAlchemy 2.0

- **Estilo declarativo 2.0**: `class X(Base): id: Mapped[int] = mapped_column(...)`. NUNCA `Column(Integer, ...)` legado.
- **`Base = DeclarativeBase`** em `db/database.py`. Reusar; não criar novas Base classes.
- **Repository pattern** em `db/repositories.py`. Services chamam repos, NUNCA executam queries direto.
- **SQLite local** com `connect_args={"check_same_thread": False}` (background tasks rodam em threadpool).
- **Sem `ON DELETE CASCADE` no SQLite por default** — `JobService.delete()` apaga manualmente em ordem: spans → review_events → job → arquivos.
- **Datetimes tz-aware no Python** (`datetime.now(timezone.utc)`), mas o SQLite armazena naive. Schema serializer reanexa UTC na saída.
- **Métodos de repo retornam None** quando não encontram (não levantam `NoResultFound`). Service decide se 404.

#### Next.js (App Router)

- **Estrutura `src/app/<rota>/page.tsx`**. Layouts em `layout.tsx`. Dynamic routes em pasta `[param]/`.
- **`params` como objeto**: assinatura `({ params }: { params: { job_id: string } })`. (No Next 15 vira Promise — não migrar sem revisar.)
- **`useRouter`** importado de `next/navigation` (NÃO `next/router`).
- **Server Components por default**, mas todas as páginas atuais usam `"use client"` por causa de estado e fetch dinâmico. Não converter sem motivo.
- **Build com `next build`** valida types automaticamente; build limpo é parte do "feito". Sem ESLint configurado.

#### Pacote `anonymizer` (core de redação)

- **Cliente PII como ABC**: `PrivacyFilterClient` em `client.py`. Implementações: `MockPrivacyFilterClient` (regex), `OpenAIPrivacyFilterClient` (OPF), `CaseNormalizingClient` (wrapper), `CompositeClient` (combine), `RegexOnlyClient` (apenas determinístico, usado pela comparação de detectores).
- **Factory canônico**: `make_augmented_client(base, *, get_enabled_kinds=...)`. NUNCA instanciar `_OverridingComposite` direto. NUNCA pular as augmentações em produção.
- **Redactor é stateful**: contadores `indexed` (`_counters`) persistem em chamadas de `redact()` no mesmo documento. Pipeline cria Redactor novo por job; chamar `reset_counters()` se reusar manualmente.
- **Pipeline orquestra fixo**: extract → detect (per block) → redact (per block) → assemble → verify → save. Não pular etapas.
- **Helper `extract_document(input_path)`** em `pipeline.py` é a forma pública de extrair blocos sem rodar redação (usado pela comparação). NUNCA reimplementar dispatch de extrator em outros módulos — chamar essa função.
- **Política como YAML** em `policies/default.yaml`. Carregar via `Policy.from_yaml(path)`. Cada `entity_type` tem `strategy` + `label`.
- **Estratégia `indexed` é o default**: produz `[NOME_01]`, `[EMAIL_02]`. Strategy padrão para PII textual; `mask`/`suppress`/`replace`/`pseudonym` legacy mas suportadas.
- **Augmentações encadeadas**: ordem importa. `CaseNormalizingClient(base)` antes de `CompositeClient` com `aux_detectors`. Override de `account_number → cpf/cnpj` aplicado no fim.
- **Detectores regex em `regex_detectors.py`** seguem padrão: regex → função `detect_<kind>(text) -> list[DetectedSpan]`. Registrados em `REGEX_DETECTORS: dict[str, callable]`.
- **Settings store** (`SettingsStore`) é a fonte de verdade do que está habilitado. NUNCA hard-codar lista de detectores. Composite filtra spans pelo `get_enabled_kinds()`.

#### Detector comparison (modo diagnóstico)

- **Núcleo em `src/anonymizer/detector_comparison.py`**: `compare_spans(opf, regex, block_id, text)` produz `list[ComparisonItem]`; `build_comparison_report(job_id, items, blocks)` agrega.
- **Status do item**: `both` (≥0.90 overlap + mesmo tipo), `type_conflict` (≥0.90 + tipo diferente), `partial_overlap` (0.30 ≤ ratio < 0.90), `opf_only`, `regex_only`. Overlap = Jaccard (intersection / union).
- **Pareamento greedy**: candidatos ordenados por overlap descendente; cada span regex é consumido por **no máximo um** OPF (regra crítica do design).
- **Lado OPF da comparação = `CaseNormalizingClient(base)`**, NÃO o base puro. Sem o wrapper o OPF não vê nomes ALL-CAPS e o diagnóstico subestima a contribuição do modelo. As augmentações regex (`br_*`) ficam só no lado regex (`RegexOnlyClient`).
- **Não altera estado do job**: o endpoint `POST /jobs/{id}/detector-comparison` re-extrai os blocos da quarentena e roda os dois clients sem tocar em `applied_spans`, `redacted.txt`, `status`, `decision`, etc. Persiste em `var/output/<job_id>/detector_comparison.json`.
- **Relatório carrega `blocks: [{block_id, text}]`** para permitir highlights na UI nos offsets autoritativos. NÃO recalcular offsets no frontend; usar `start`/`end` direto.
- **Logs do módulo só carregam metadados** (counts, block_id, ratios). NUNCA logar `text`, `text_preview` ou `context_preview`.

### Testing Rules

- **Tests em `tests/` na raiz**, NUNCA dentro de `src/`. `pythonpath = ["src"]` em `pyproject.toml` resolve imports.
- **292 testes; manter verde**. Cobertura por arquivo: `test_api.py` (827), `test_augmentations.py` (~750), `test_reversible.py` (305), `test_pipeline.py` (299), `test_redactor.py` (279), `test_rules.py` (227), `test_privacy_filter_client.py` (190), `test_regex_detectors.py` (178), `test_extractors.py` (171), `test_settings.py` (149), `test_risk.py` (146), `test_verification.py` (68).
- **Apenas dados sintéticos**. NUNCA usar PII real em fixture ou teste. CPFs/CNPJs gerados via helpers `_make_cpf` / `_make_cnpj` que respeitam o algoritmo de DV.
- **Fixtures em `tests/conftest.py`**: `synthetic_txt`, `synthetic_md`, `synthetic_docx`, `synthetic_xlsx`. Cada uma escreve em `tmp_path`.
- **TestClient do FastAPI** com fixture `api_client` que cria um `Settings` em `tmp_path` e usa `use_mock_client=True`. NUNCA acionar OPF real em testes.
- **Mock do OPF por injeção direta**: `client._model = _make_fake_model([...])`, NÃO via `patch.dict("sys.modules", {"opf": ...})` — esse padrão antigo quebrou ao instalar o opf de verdade.
- **`device="cpu"` em testes** que instanciam `OpenAIPrivacyFilterClient` — evita carregar torch desnecessário.
- **Helpers de span**: função `span(start, end, entity_type)` cria `DetectedSpan` com confidence 0.9. Reusar.
- **`monkeypatch` para env vars / `sys.modules`**, `unittest.mock` para classes. Usar conforme o caso.
- **Testes assíncronos** raros — `BackgroundTasks` do FastAPI roda dentro do `with TestClient` antes do response retornar. Helper `_wait_until_complete()` em `test_api.py` poll o status até completar.
- **Sem cobertura mínima fixada**, mas testes novos devem cobrir caminho feliz + ≥1 caso de erro.
- **Build TypeScript** (`npx --no-install next build`) valida types — também faz parte do "feito" para mudanças no frontend.

### Code Quality & Style Rules

- **Sem linter/formatter configurado** (Black, Ruff, ESLint, Prettier não estão no projeto). Seguir PEP 8 e convenções de TS/React manualmente.
- **Indentação**: 4 espaços (Python), 2 espaços (TS/TSX/CSS/JSON/YAML).
- **Linha máxima ~88–100 chars**. Quebrar argumentos longos em múltiplas linhas com fechamento alinhado.
- **Imports ordenados**: stdlib → terceiros → locais (relativos). Branch entre grupos com linha em branco.
- **Naming**:
  - Python: `snake_case` para funções/vars/módulos, `PascalCase` para classes, `UPPER_SNAKE` para constantes módulo-level, `_underscore` prefix para privados.
  - TypeScript: `camelCase` para vars/funções, `PascalCase` para componentes/tipos/interfaces, `UPPER_SNAKE` para constantes top-level.
  - Arquivos: `snake_case.py`, `kebab-case.tsx` para páginas, `PascalCase.tsx` para componentes.
- **Mensagens de erro em inglês no código**, mensagens visíveis ao usuário em **PT-BR** (toasts, banners, labels, instruções).
- **Logs estruturados**: `logger.info("event_name key1=%s key2=%d", v1, v2)`. NUNCA logar segredos, conteúdo de doc, fragmentos PII ou substituições. Apenas: `job_id`, `block_id`, `span_index`, hashes, posições, tipo, pontuação.
- **Nomes descritivos > comentários**. Função `detect_cpfs` em vez de função `f` com comentário "detecta CPFs".
- **Sem TODO/FIXME** misturados em código de produção; abrir issue ou descrever no PR.
- **Frontmatter YAML** em arquivos `.md` opcionais; quando presentes, mantê-los atualizados.

### Development Workflow Rules

- **Setup completo**: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,api,ml]"`. Modelo OPF é baixado no primeiro `detect()` (~2.6 GB → `~/.opf/privacy_filter/`).
- **Subir tudo**: `./start-anom.sh` — sobe API (porta 9000) + UI (porta 3000) com OPF real. Flags: `--mock` (regex), `--no-ui` (só backend), `--reset` (apaga `./var/`), `--port` / `--ui-port`.
- **venv obrigatório**: comandos Python sempre via `.venv/bin/python` ou `.venv/bin/pytest` ou `.venv/bin/pip`. NUNCA usar Python global.
- **`./var/`** é estado runtime (não versionado): `quarantine/`, `output/`, `anonymizer_api.db`, `runtime_config.json`. Apagável com `--reset`.
- **Reprocessar um job** = apagar pela UI (botão ✕) e reenviar. Não há in-place re-process. Para mudar status legado: SQL direto na DB.
- **Frontend dev**: `cd apps/reviewer-ui && npm install` na 1ª vez. `npm run dev` se rodar isolado, ou via `start-anom.sh`. Build de validação: `npx --no-install next build`.
- **CORS**: API libera origens do `cors_origins` da Settings. UI dev em `localhost:3000` está incluído por default.
- **Configuração runtime via `/settings`**: enabled/disabled de detectores via UI ou `PUT /settings`. Não editar `runtime_config.json` à mão se possível — passa pela API pra validar.
- **Política em YAML** (`policies/default.yaml`) é editável; mudanças não afetam jobs já processados, só novos uploads.
- **Sem CI configurado**. Antes de mergear: `pytest -q && cd apps/reviewer-ui && npx --no-install next build`.
- **Git**: NUNCA fazer commits sem o usuário pedir explicitamente. Política do harness — vale para qualquer agente trabalhando aqui.

### Critical Don't-Miss Rules

#### Privacidade (não negociável)

1. **NUNCA logar texto bruto, conteúdo de documento, fragmento PII ou substituição.** Apenas: `job_id`, `block_id`, `span_index`, hashes (SHA-256), posições, `entity_type`, score, contagens. Verificável por inspeção: `grep -nR "logger" src/` deve mostrar apenas formats com `%s`/`%d` em campos de metadados.
2. **`text_hash` é SHA-256 do conteúdo case-folded + whitespace-collapsed.** Reusar `_hash_fragment()` ou `augmentations._hash()` — não criar variantes.
3. **Documento original fica em `var/quarantine/<job_id><ext>`** (acesso apenas pela API). Spans expõem `original_text` no relatório por necessidade de revisão — esse é um trade-off **deliberado** com aprovação explícita do usuário.

#### Posições e offsets

4. **Spans carregam `redacted_start`/`redacted_end` autoritativos.** Frontend usa esses campos direto; pipeline e service mantêm consistência. NUNCA recalcular via delta math no frontend se o backend já forneceu.
5. **Redação manual usa find-and-replace-all** com `expected_text` como fonte de verdade. NUNCA usar só os offsets — eles podem estar stales. Se `expected_text` não bater, retornar 400.
6. **False positive (revert)** restaura o original, marca `false_positive: true`, e desloca posições subsequentes pelo `delta`. Idempotência: chamar 2x retorna 400.
7. **Quebra de linha não é separador no regex de nomes BR**. Usar `[ \t]+`, NUNCA `\s+` (este último vaza nomes para a linha seguinte).

#### Estado e sincronização

8. **Redactor é stateful por documento**. Pipeline cria Redactor novo por job. Não compartilhar instâncias entre jobs sob nenhuma circunstância.
9. **Background tasks abrem própria DB session** — a session do request foi fechada pelo teardown de `Depends`. Reuse causa erro de "session is closed".
10. **Settings store é cacheado em memória, thread-safe.** Não ler arquivo direto; sempre usar `store.get()` / `store.update()`.
11. **Datetimes serializados precisam de offset UTC explícito.** SQLite perde tzinfo no round-trip; schemas Pydantic reanexam via `field_serializer`. Sem isso o navegador interpreta como local e a hora fica errada (3h adiantada para UTC-3).

#### Status e fluxo

12. **Não existe mais status `blocked` automático.** Conteúdo crítico (JWT, secret, CPF, etc.) vai para `awaiting_review` com `risk_level=critical`. Decisões possíveis: `auto_approve`, `sample_review`, `manual_review`.
13. **TODO documento processado vai para `awaiting_review`**, mesmo de risco baixo. `decision` e `risk_level` são apenas sinais visuais para o revisor — nunca pulam revisão. `STATUS_AUTO_APPROVED` permanece como tipo válido apenas por compatibilidade com jobs legados.
14. **Approve/Reject só funcionam em `awaiting_review`**. Outros estados retornam 409.
15. **Download só funciona em `auto_approved` (legado) ou `approved`**. Demais estados → 403.
16. **Delete recusa `pending`/`processing`** (race com worker). Tudo o mais é apagável.

#### Detecção e regras

16. **CPF/CNPJ vencem `account_number`**: validador de DV é mais confiável que classificação do modelo. Override aplicado em `_override_generic_with_specific`.
17. **Indexed dedupe é case + whitespace insensitive**: `"Maria Silva"` ≡ `"MARIA SILVA"` ≡ `"maria  silva"` → mesmo `[NOME_01]`.
18. **Detectores regex genéricos exigem keyword** (RG, CNH, RENAVAM, SUS, IE etc.) para evitar falsos positivos em sequências numéricas. NÃO afrouxar sem revisar custo de FP.
19. **OPF não baixa modelo automaticamente via Python API** — só via CLI `python -m opf redact`. Por isso `make_augmented_client` não tenta baixar; documento de setup orienta a rodar a CLI uma vez.
20. **`account_number` permanece como tipo válido** para casos não-CPF/CNPJ (IBANs, contas bancárias estrangeiras). Não removê-lo da política.

#### Backwards compatibility

21. **Spans antigos sem `redacted_start`/`redacted_end`** são backfillados em `_ensure_redacted_positions()` antes de qualquer operação que os movimente. NUNCA assumir que esses campos existem em dados antigos sem checar.
22. **Spans antigos sem `original_text`**: frontend cai no fallback de mostrar contexto do redigido. Backend retorna 400 em revert se faltar (não tem como restaurar sem ele).

#### Modos de processamento (anonymization vs reversible_pseudonymization)

23. **Dois modos no upload**, escolhido pelo usuário:
    - `anonymization` (default) — irreversível, comportamento histórico
    - `reversible_pseudonymization` — permite restaurar dados originais
24. **Mesmo pipeline para os dois modos** — a diferença é só se os endpoints `/reversible/*` ficam disponíveis. NÃO duplicar lógica de extração/redação.
25. **Endpoints reversíveis** ficam em `/jobs/{id}/reversible/{package|validate|restore|download|status}`. Recusam jobs em modo `anonymization` com 400.
26. **Termo "Preparar para LLM" é proibido** na UI/API. Os dois modos podem alimentar um LLM; o que muda é a reversibilidade. Use "Restaurar dados originais", nunca "rehydrate".
27. **Validação verifica três coisas**: marcadores ausentes (count menor que esperado), duplicados (count maior), inesperados (padrões `[XXX_NN]` que não vieram do original).
28. **Restore persiste em `output_dir/{job_id}/restored.txt`** e atualiza `job.restored_path`. Cada chamada sobrescreve o arquivo anterior.
29. **Spans `false_positive` são ignorados** no pacote reversível — o original já está no texto, não há marcador para restaurar.
30. **Migração SQLite via `_ensure_column`**: Postgres usaria Alembic. Para SQLite, `Database.create_all()` faz `ALTER TABLE` defensivo nas colunas novas (mode, restored_path).

#### Modo diagnóstico (Comparação de detectores)

31. **Comparação NÃO é um JobMode**. Backend só conhece `anonymization` e `reversible_pseudonymization`. A "terceira opção" no upload da UI é UI-only — sobe como anonymization e dispara `POST /jobs/{id}/detector-comparison` automaticamente após processar. Sinalizada via query string `?autocompare=1`.
32. **POST/GET `/jobs/{id}/detector-comparison`** nunca alteram `status`, `decision`, `risk_level` ou qualquer artefato do job. Idempotência: chamar POST 2x simplesmente sobrescreve o JSON salvo.
33. **OPF da comparação = OPF + `CaseNormalizingClient`, nada mais.** NÃO incluir `br_labeled_name`, `br_cpf`, etc. naquele lado — eles são o que o `RegexOnlyClient` representa. Misturar inverte o sinal do diagnóstico.
34. **Números da comparação ≠ stats da revisão.** Comparação mede detecções cruas dos dois lados; stats da revisão mede `applied_spans` pós-overlap-resolution. Diferença é esperada (especialmente para CPF/CNPJ que ganham de `account_number`).
35. **`text_preview` / `context_preview` ficam no relatório, NUNCA em log.** Janelas pequenas (≤60 / ±24 chars). Persistidos em `var/output/<job_id>/detector_comparison.json` — esse diretório é tratado como zona segura, no mesmo nível da quarentena.
36. **UI cai pra texto puro se offsets conflitarem** (ranges sobrepostos no mesmo bloco). NÃO crashar. Cada bloco é independente: um bloco com conflito não derruba o resto.

---

## Métricas do projeto

Snapshot em **2026-05-01**. Atualizar quando houver mudanças significativas
(±20% em alguma categoria) — útil para agentes calibrarem o tamanho do que
estão tocando.

| Categoria | LOC |
|---|---:|
| Python — core (`src/anonymizer/`) | 2.744 |
| Python — API FastAPI (`src/anonymizer_api/`) | 2.673 |
| Python — testes (`tests/`) | 4.301 |
| Python — scripts CLI (`scripts/`) | 351 |
| **Python total** | **10.069** |
| TypeScript / TSX (`apps/reviewer-ui/src/`) | 4.680 |
| CSS (`globals.css`) | 1.236 |
| **Frontend total** | **5.916** |
| YAML (políticas) | 156 |
| Config / build / shell | 329 |
| Markdown (docs) | 668 |
| **TOTAL GERAL** | **~17.140** |

**319 testes Python** passando. Cobertura por área (LOC de testes / LOC de
produção Python ignorando testes): ~75% — testes ocupam volume comparável
ao código de produção.

### Maiores arquivos (referência rápida para refatoração)

**Python (produção)**
- `src/anonymizer_api/jobs/service.py` — 1.188 LOC. Orquestrador principal:
  upload, processing, approve/reject, manual redaction, revert, reversible
  workflow, delete, detector comparison. Candidato a desmembrar se passar
  de 1.300 (split natural: review actions / reversible / detector-comp).
- `src/anonymizer/augmentations.py` — 502 LOC. Wrappers do PrivacyFilterClient
  + override CPF/CNPJ + nomes BR rotulados + endereços.
- `src/anonymizer_api/routers/jobs.py` — 480 LOC. Endpoints de jobs +
  reversible + reviewer actions. Detector-comparison vive em router próprio.
- `src/anonymizer/detector_comparison.py` — 426 LOC. Núcleo da comparação
  OPF vs regex (compare_spans + build_comparison_report + dataclasses).

**Python (testes)**
- `tests/test_api.py` — 897 LOC. Cobre fluxo end-to-end via TestClient.
- `tests/test_augmentations.py` — 713 LOC. Detectores BR + override + e2e.
- `tests/test_detector_comparison.py` — 347 LOC. Cobre todos os 5 status
  + agregação por tipo + privacidade nos logs.
- `tests/test_pipeline.py` — 312 LOC.

**Frontend**
- `apps/reviewer-ui/src/app/jobs/[job_id]/review/page.tsx` — 949 LOC.
  Tela de revisão com seleção manual + popover. Maior componente.
- `apps/reviewer-ui/src/components/DetectorComparisonPanel.tsx` — 635 LOC.
  Painel diagnóstico (cards, filtros, tabela, integração com text view).
- `apps/reviewer-ui/src/lib/api.ts` — 413 LOC. Cliente HTTP, modo mock e
  toda chamada de API do frontend.
- `apps/reviewer-ui/src/app/globals.css` — 1.236 LOC. Sistema de design
  inteiro num arquivo só. Considerar split por componente se passar de 1.500.

---

## Usage Guidelines

### Para agentes de IA

- **Ler este arquivo antes de implementar** qualquer código novo neste projeto.
- **Seguir TODAS as regras como documentadas**. Se houver conflito entre regra e instrução pontual, perguntar antes.
- **Em caso de dúvida, escolher a opção mais restritiva** (mais privacidade, menos exposição, menos magic).
- **Atualizar este arquivo** quando padrões novos surgirem ou regras existentes ficarem obsoletas. PR descrevendo a mudança.

### Para humanos

- **Manter enxuto e focado em coisas não óbvias**. Se uma regra ficou óbvia (todo agente já segue), apagar.
- **Atualizar quando a stack mudar** (versões maiores, framework novo).
- **Revisão trimestral** para podar regras stale.

---

_Última atualização: 2026-05-01_
