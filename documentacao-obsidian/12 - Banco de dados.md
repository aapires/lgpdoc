---
tags: [lgpdoc, banco, sqlite, migrations]
---

# Banco de dados

SQLite local em `var/anonymizer_api.db`. Pequeno propósito: nada mais sofisticado precisava aqui.

## Stack

- **SQLAlchemy 2.0** estilo declarativo — `Mapped`, `mapped_column`. Nunca `Column(Integer, ...)` legado.
- `Base = DeclarativeBase` em `db/database.py`.
- Repository pattern em `db/repositories.py` — services chamam repos, repos fazem query. Services não escrevem SQL nem usam `session.query()` direto.

## Modelos

### `JobModel` — `jobs`
Job de documento avulso.

```python
job_id          str     # primary key, UUID4
status          str     # pending | processing | awaiting_review | approved | rejected | failed | auto_approved (legado)
mode            str     # anonymization | reversible_pseudonymization
source_filename str
file_hash       str(64) # SHA-256 do arquivo
file_size       int
file_format     str(10)
quarantine_path str
created_at      datetime  # tz-aware UTC
updated_at      datetime
completed_at    datetime?
redacted_path   str?
spans_path      str?
metadata_path   str?
report_path     str?
restored_path   str?     # output do reversível restore
decision        str?     # auto_approve | sample_review | manual_review
risk_level      str?     # low | medium | high | critical
risk_score      float?
error_message   str?
opf_used        bool?    # True se OPF estava on no processing; None = legado
policy_version_id  int?  # FK para PolicyVersionModel
container_id    str?     # FK para ContainerModel; null = job avulso
```

### `DetectedSpanModel` — `detected_spans`
Spans aplicados de um job. Reflete o `spans.json` em formato consultável.

### `ReviewEventModel` — `review_events`
Eventos de revisão: aprovação, rejeição, edits, false_positive, comments. Persistido para audit trail.

### `PolicyVersionModel` — `policy_versions`
Versionamento da política. Cada hash distinto de `default.yaml` recebe um `id`. Jobs apontam pra qual versão usaram.

### `ContainerModel` — `containers`
Detalhes em [[08 - Containers]].

### `ContainerDocumentModel` — `container_documents`
Documento dentro de um container. Tem `job_id` (driver do pipeline) ou null (já-pseudonimizado).

### `ContainerMappingEntryModel` — `container_mapping_entries`
A tabela `(marker, original_value)`. **Fonte de verdade** dos pseudônimos do container.

```
UniqueConstraint(container_id, marker)              — marker é único dentro do container
Index(container_id, entity_type, normalized_value)  — para o resolver
```

### `ContainerSpanModel` — `container_spans`
Ocorrências de cada mapping entry em cada documento do container.

## Migrations

> [!info] Sem Alembic
> Em SQLite, o schema é evoluído com `_ensure_column()` defensivo em `db/database.py`:

```python
def create_all(self):
    Base.metadata.create_all(self.engine)
    if self.url.startswith("sqlite"):
        self._ensure_column("jobs", "mode", "VARCHAR(40) NOT NULL DEFAULT 'anonymization'")
        self._ensure_column("jobs", "restored_path", "VARCHAR")
        self._ensure_column("jobs", "container_id", "VARCHAR(36)")
        self._ensure_column("jobs", "opf_used", "BOOLEAN")
        self._ensure_column("container_documents", "job_id", "VARCHAR(36)")
```

`_ensure_column` checa via `PRAGMA table_info(table)` e faz `ALTER TABLE` se a coluna não existe. Idempotente. Para Postgres a gente usaria Alembic; para SQLite isso basta.

## Sessão por request

```python
# src/anonymizer_api/deps.py
def get_db() -> Session:
    db = app.state.database.session()
    try:
        yield db
        db.commit()
    finally:
        db.close()
```

> [!warning] Background tasks abrem própria sessão
> A session do request é fechada pelo teardown do `Depends(get_db)` antes do background rodar. Reutilizar daria "session is closed". Padrão correto:
> ```python
> def _run_processing(app, job_id):
>     db = app.state.database.session()
>     try:
>         service = app.state.service_factory(db)
>         service.process(job_id)
>     finally:
>         db.close()
> ```

## Datetimes

> [!warning] SQLite perde tzinfo
> SQLAlchemy + SQLite armazena datetime "naive" (sem timezone) no round-trip. O Python sempre cria com `datetime.now(timezone.utc)`, mas o que sai do banco é naive.
>
> Solução: schemas Pydantic reanexam `+00:00` via `field_serializer`. Sem isso, o navegador interpreta como local time e a hora fica errada (3h adiantada para UTC-3). Vê `_iso_utc` em `schemas.py`.

## Inspeção manual

Para inspecionar o banco fora da app (debug):

```bash
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('var/anonymizer_api.db')
for row in con.execute('SELECT job_id, status, opf_used, created_at FROM jobs ORDER BY created_at DESC LIMIT 10'):
    print(row)
"
```

Ou abre com [DB Browser for SQLite](https://sqlitebrowser.org/) (`var/anonymizer_api.db`).

## Reset

Para zerar o banco (e os arquivos):

```bash
./start-anom.sh --reset
```

Apaga `var/` inteiro antes de subir. Cuidado — leva quarentena, output e DB junto.

Próximo: [[13 - Privacidade]].
