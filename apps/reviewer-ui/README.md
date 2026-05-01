# LGPDoc Reviewer UI

Next.js 14 (App Router) + TypeScript frontend para revisar documentos
anonimizados (produto chamado **LGPDoc**, codinome interno do pacote
Python: `anonymizer`). Sem framework de estilo — só CSS plain em
`src/app/globals.css` (sistema de design completo com tokens CSS).

## Pages

- `/jobs` — lista de documentos em cards, com upload integrado e seletor
  de modo (Anonimização / Pseudonimização reversível)
- `/jobs/[job_id]` — detalhe do job com hero header, painel reversível
  (se aplicável), resumo da verificação e detalhes técnicos colapsados
- `/jobs/[job_id]/review` — revisão interativa span por span: texto
  redigido com highlights, lista de trechos detectados, seleção manual
  para anonimizar, aprovar/rejeitar
- `/settings` — habilitar/desabilitar tipos de detecção (CPF, CNPJ, RG,
  CNH, OAB, ...)

## Setup

```bash
cd apps/reviewer-ui
npm install
cp .env.example .env.local      # ajusta API URL ou habilita mocks
npm run dev                     # http://localhost:3000
```

Ou simplesmente `./start-anom.sh` na raiz — sobe API + UI juntos.

## Modos de execução

| Variável | Efeito |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | URL do FastAPI (default `http://127.0.0.1:9000`) |
| `NEXT_PUBLIC_USE_MOCKS=true` | Pula a rede — usa `src/lib/mocks.ts`. Útil pra trabalhar visual sem subir o backend. |

## API endpoints consumidos

| Método | Path | Usado por |
|---|---|---|
| `GET` | `/jobs` | Lista de jobs |
| `POST` | `/jobs/upload` | Envio de novo documento |
| `GET` | `/jobs/{id}` | Detalhe do job |
| `GET` | `/jobs/{id}/report` | Payload de revisão (verificação + texto + spans) |
| `POST` | `/jobs/{id}/review-events` | Ações por span (accept / edit / comment / missed_pii) |
| `POST` | `/jobs/{id}/spans/{idx}/revert` | Marcar span como falso positivo (restaura original) |
| `POST` | `/jobs/{id}/manual-redactions` | Anonimização manual via seleção (find-and-replace-all) |
| `POST` | `/jobs/{id}/approve` | Aprovação final |
| `POST` | `/jobs/{id}/reject` | Rejeição final |
| `DELETE` | `/jobs/{id}` | Exclusão definitiva |
| `GET` | `/jobs/{id}/download` (link) | Arquivo redigido final |
| `POST` | `/jobs/{id}/reversible/package` | Modo reversível: obter pacote |
| `POST` | `/jobs/{id}/reversible/validate` | Modo reversível: validar texto processado |
| `POST` | `/jobs/{id}/reversible/restore` | Modo reversível: restaurar dados originais |
| `GET` | `/jobs/{id}/reversible/download` (link) | Modo reversível: baixar texto restaurado |
| `GET` | `/jobs/{id}/reversible/status` | Modo reversível: estado do fluxo |
| `GET`/`PUT` | `/settings` | Configuração de detectores ativos |

## Modos de tratamento

Ao subir um documento, escolha entre:

- **🔒 Anonimização** — irreversível, dados sensíveis são substituídos por
  placeholders indexados (`[PESSOA_01]`, `[EMAIL_02]`, `[CPF_01]`).
- **🔄 Pseudonimização reversível** — mesma substituição, mas o sistema
  preserva o mapeamento e expõe o fluxo de restauração (útil para passar
  o texto por LLMs externos e depois trazer os dados de volta).

## Detecção e privacidade

- O **texto original** do documento **nunca** é carregado pela UI. Só
  trafega o texto redigido (com placeholders no lugar dos dados sensíveis).
- A exceção controlada: cada span carrega seu `original_text` para a
  revisão julgar a substituição. Isso vem do backend e fica visível só na
  tela de revisão.
- Toda ação por span (accept, edit, false_positive, comment, missed_pii)
  e por documento (approve, reject) gera um `ReviewEvent` no backend
  (audit trail completo).
- Anonimização manual é **find-and-replace-all** sobre o texto selecionado:
  todas as ocorrências do mesmo trecho ficam com o mesmo placeholder.
- Marcar como falso positivo restaura o original no documento e aplica
  fundo laranja claro no realce — a operação é registrada e desloca os
  offsets dos spans subsequentes automaticamente.

## Sistema de design

Tokens em `globals.css`:
- Paleta: superfícies (bg, surface, surface-soft, border), brand (accent
  azul), status (green/yellow/orange/red com tints suaves)
- Sombras (`--shadow-sm/-md/-lg`)
- Tipografia (font sans + mono, line-height 1.55, letter-spacing -0.02em
  em títulos)
- Border-radius (`6px / 10px / 14px / full`)
- Transições padrão `0.15s`

Componentes-chave: `JobCard`, `UploadCard` (com mode-tabs), `ReversiblePanel`,
`StatusBadge`/`RiskBadge`/`ModeBadge`, `AppHeader` sticky com blur.

## Span positioning

Cada span carrega `redacted_start`/`redacted_end` autoritativos, calculados
pelo backend. Frontend lê esses campos diretamente — não recalcula via
delta math. Quando uma redação manual ou false positive é aplicada, o
backend reescreve o arquivo e ajusta os offsets de todos os spans
subsequentes em uma única operação atômica. Veja `computeRedactedOffsets`
em `src/app/jobs/[job_id]/review/page.tsx` (mantém um fallback delta-math
para dados legados sem os campos novos).
