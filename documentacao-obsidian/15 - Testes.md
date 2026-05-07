---
tags: [lgpdoc, testes, pytest, qa]
---

# Testes

Pytest na raiz (`tests/`), `pythonpath = ["src"]` no `pyproject.toml`. Total: ~626 testes Python passando + build TypeScript.

## Comandos

```bash
.venv/bin/pytest -q                       # rodar tudo
.venv/bin/pytest -q tests/test_api.py     # arquivo específico
.venv/bin/pytest -q -x                    # parar no 1º erro
.venv/bin/pytest -q tests/test_X.py::TestY::test_z  # caso específico
.venv/bin/pytest --cov --cov-report=term-missing
```

UI:
```bash
cd apps/reviewer-ui && npx --no-install next build
```

> [!warning] Validação antes de declarar feito
> **Sempre** rode os dois (`pytest -q` e `next build`) antes de considerar uma mudança pronta.

## Estrutura

```
tests/
├── conftest.py                  # fixtures: synthetic_txt, synthetic_docx, synthetic_xlsx, synthetic_xls, synthetic_md
├── fixtures/                    # arquivos sintéticos pesados, se necessário
├── test_api.py                  # API end-to-end (~900 LOC, mais largo)
├── test_api_detector_comparison.py
├── test_augmentations.py        # detect_br_labeled_names + composite + e2e
├── test_extractors.py
├── test_redactor.py
├── test_pipeline.py
├── test_privacy_filter_client.py
├── test_regex_detectors.py
├── test_reversible.py           # modos reversíveis
├── test_settings.py
├── test_risk.py
├── test_verification.py
├── test_rules.py
├── test_log_privacy.py          # invariantes de privacidade nos logs
├── test_mode_separation.py      # arquitetura: containers ↮ jobs
├── test_ocr.py
├── test_markdown_helper.py
├── test_ui_invariants.py        # invariantes do source TS
├── test_opf_manager.py          # subprocesso, manager, watchdog
├── test_job_reprocess.py        # reprocess endpoint + opf_used
├── test_container_*.py          # documentos, marker resolver, normalizers, restore, validation, export
├── test_containers_api.py
└── test_detector_comparison.py
```

## Convenções

### Apenas dados sintéticos

> [!warning] Nunca PII real em fixture ou teste
> CPFs/CNPJs gerados via `_make_cpf` / `_make_cnpj` (algoritmo de DV correto). Nomes inventados ("Joao Silva", "Maria Pereira"). Nunca colar dado de cliente.

### TestClient + `use_mock_client`

```python
@pytest.fixture()
def api_settings(tmp_path: Path) -> Settings:
    return Settings(
        quarantine_dir=tmp_path / "quarantine",
        output_dir=tmp_path / "output",
        db_url=f"sqlite:///{tmp_path}/api.db",
        ...,
        use_mock_client=False,
        opf_use_mock_worker=True,
    )

@pytest.fixture()
def api_client(api_settings: Settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as client:
        yield client
```

> [!info] Por que `opf_use_mock_worker=True` ao invés de `use_mock_client=True`
> O primeiro mantém OPF "available" mas com o subprocesso rodando MockClient — exercita o pipeline da subprocess sem `torch`/`opf` instalados. O segundo desabilita OPF inteiramente (toggle invisível, comparação retorna 409).
>
> Use `opf_use_mock_worker=True` quando o teste exercita features que dependem do OPF estar disponível (comparação, badges de `opf_used`, reprocess com OPF on).

### Mock do OPF — não via `sys.modules`

> [!warning] Padrão antigo quebrado
> NÃO use `monkeypatch.setitem(sys.modules, "opf", fake)`. Esse padrão quebra quando o `opf` real está instalado.
>
> Padrão correto: injeção direta no instance — `client._model = _make_fake_model([...])`. Vê `tests/test_privacy_filter_client.py`.

### Helper `span()`

```python
from tests.helpers import span  # ou onde estiver
s = span(0, 10, "private_person")  # confidence=0.9 default
```

### Background tasks

> [!info] BackgroundTasks no TestClient
> O FastAPI roda background tasks **dentro** do `with TestClient(app):` antes do response retornar. Para esperar processamento async em testes:
>
> ```python
> def _wait_until_complete(client, job_id, timeout=5.0):
>     deadline = time.monotonic() + timeout
>     while time.monotonic() < deadline:
>         body = client.get(f"/jobs/{job_id}").json()
>         if body["status"] not in ("pending", "processing"):
>             return body
>         time.sleep(0.05)
>     raise AssertionError(...)
> ```

### Invariantes de privacidade

`tests/test_log_privacy.py` percorre os flows críticos com `caplog` e checa que nenhum fragmento PII vazou. Cada feature nova deve ter cobertura aqui se houver risco de log leak.

### Invariantes arquiteturais

- `tests/test_mode_separation.py::TestContainersIsolatedFromJobsService` — containers e jobs não importam um do outro.
- `tests/test_ui_invariants.py` — convenções do source TypeScript.

## Cobertura por área (LOC tests/produção)

```
test_api.py            ~900 LOC
test_augmentations.py  ~750
test_reversible.py     ~300
test_pipeline.py       ~310
test_redactor.py       ~280
test_rules.py          ~225
test_privacy_filter_client.py ~190
test_regex_detectors.py ~180
test_extractors.py     ~170
test_settings.py       ~150
test_risk.py           ~145
test_verification.py    ~70
test_opf_manager.py    ~340 (com idle watchdog)
test_job_reprocess.py  ~170
```

Cobertura aproximada (testes / produção, ignorando os scripts CLI): ~75%. Os testes ocupam volume comparável ao código de produção.

## Testes novos

- Sempre cobrir caminho feliz + ≥1 caso de erro.
- Adicionar a `test_log_privacy.py` se a feature pode logar.
- Se for invariant arquitetural, adicionar a `test_mode_separation.py` ou `test_ui_invariants.py`.
- Testes de UI invariants checam **strings literais no source** (`Reportar PII não detectada`, etc) — quando a UI muda, o teste precisa acompanhar.

## Sem CI ainda

> [!info] Validação manual antes de mergear
> Não há GitHub Actions configurado. Cada commit é responsabilidade do dev: `pytest -q` + `next build` precisam estar verdes.

Próximo: [[16 - Desenvolvimento]].
