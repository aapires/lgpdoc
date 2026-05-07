---
tags: [lgpdoc, frontend, nextjs, react]
---

# Frontend

Next.js 14 App Router em `apps/reviewer-ui/`. CSS plain — sem Tailwind, Material, shadcn ou similares.

## Stack

| Tech | Versão | Notas |
|---|---|---|
| Next.js | ^14.2.0 | App Router, **não** Pages Router. Upgrade pra 15 requer revisar `params` que vira Promise. |
| React | ^18.3.0 | |
| TypeScript | ^5 | `strict: true`. `any` é proibido — use `unknown`. |
| CSS | plain | `src/app/globals.css`, ~1.6k linhas, design system inteiro. |

## Estrutura

```
apps/reviewer-ui/src/
├── app/                       # rotas (App Router)
│   ├── layout.tsx             # AppHeader + brand + OpfToggle
│   ├── globals.css            # design system
│   ├── page.tsx               # redirect para /jobs
│   ├── jobs/
│   │   ├── page.tsx           # lista de documentos
│   │   └── [job_id]/
│   │       ├── page.tsx       # detalhe do documento
│   │       └── review/
│   │           └── page.tsx   # tela de revisão (a maior)
│   ├── containers/
│   │   ├── page.tsx
│   │   ├── new/page.tsx
│   │   └── [containerId]/
│   │       ├── page.tsx
│   │       ├── mapping/page.tsx
│   │       ├── restore/page.tsx
│   │       └── documents/[documentId]/review-pseudonymized/page.tsx
│   └── settings/page.tsx
├── components/
│   ├── AppHeader.tsx          # nav + OpfToggle
│   ├── OpfToggle.tsx          # botão runtime do modelo
│   ├── OpfModeBadge.tsx       # badge "🤖 OPF" / "📋 só regex"
│   ├── StatusBadge.tsx        # status/risk/mode badges
│   ├── UploadCard.tsx         # dropzone + escolha de modo
│   ├── JobContentPanel.tsx
│   ├── DetectorComparisonPanel.tsx
│   ├── ComparisonTextView.tsx
│   └── ReversiblePanel.tsx
└── lib/
    ├── api.ts                 # cliente HTTP, ~840 LOC
    ├── types.ts               # espelha schemas Pydantic
    ├── mocks.ts               # USE_MOCKS=true para dev offline
    └── sources.ts             # source → emoji/label
```

## Convenções

- **`"use client"` no topo** de toda página/componente que usa `useState`, `useEffect`, event handlers, ou `window.*`. Componentes server-side são default mas raros aqui.
- **Path alias `@/`** → `./src/`. Imports devem ser `@/lib/...`, `@/components/...`. Nunca `../../../`.
- **Tipos em `src/lib/types.ts`** espelhando schemas do backend. Manter em sync ao mudar API.
- **Cliente API em `src/lib/api.ts`** com `fetchJSON<T>()` helper. Toda chamada passa por lá; nada de `fetch()` em componentes.
- **CSS via `className`**. `style={...}` inline só para valores dinâmicos (posição de popover etc).
- **Componentes funcionais** apenas. Nunca `class Component extends`.
- **`router.push()`** para navegação programática, `<Link>` para declarativa. `useRouter` de `next/navigation` (não `next/router`).
- **Cleanup em `useEffect`**: retornar função para `clearTimeout`, `removeEventListener`. Padrão obrigatório.

## Modo mock para dev offline

`NEXT_PUBLIC_USE_MOCKS=true` em `.env.local` faz `api.ts` retornar dados de `src/lib/mocks.ts`. Toda função em `api.ts` precisa de ramo `if (USE_MOCKS) {...}`.

Útil para mexer no UI sem ter o backend subindo.

## OpfToggle — particularidades

[[06 - OPF runtime toggle|Componente do header]]. Três estados visuais:

| Estado | Visual | Texto |
|---|---|---|
| Off | bolinha cinza | `OpenAI Privacy Filter OFF` |
| Loading | bolinha amarela pulsante | `OpenAI Privacy Filter loading…` |
| On | bolinha verde + countdown | `OpenAI Privacy Filter ON · 04:32` |

Polling adaptativo:
- Loading → 1.5 s (capturar transição para ready)
- On → 5 s (atualizar countdown)
- Off → não poll

Hidden quando `available=false` (modo `--mock`).

## Tela de revisão — a maior

`apps/reviewer-ui/src/app/jobs/[job_id]/review/page.tsx` (~950 LOC). Combina:

- Header com filename, status, OPF badge, **botão "🔁 Reprocessar"**.
- Painel esquerdo: texto redigido com highlights por span. Suporta seleção manual → "Anonimizar".
- Painel direito: ações de aceitar/rejeitar + lista de spans com per-span actions.
- Stats de detecção (modelo vs regras) num `<details>` colapsável.

## Tela de detalhe

`apps/reviewer-ui/src/app/jobs/[job_id]/page.tsx`. Mais simples — hero com badges + painel central com `JobContentPanel` ou `ReversiblePanel` ou `DetectorComparisonPanel` conforme contexto.

## Build

```bash
cd apps/reviewer-ui
npx --no-install next build  # validação de tipos + bundle
npm run dev                  # dev server (auto-reload)
```

> [!info] Sem ESLint/Prettier configurados
> Convenções aplicadas manualmente. PRs que adicionarem isso são bem-vindos, desde que não causem churn massivo de formatação.

## Telas onde o OPF toggle aparece

Por design, o toggle precisa estar em toda tela onde o usuário pode disparar processamento. Como ele vive no `AppHeader` (que está no `layout.tsx`), está em **todas** as telas. ✅

## Telas onde o OpfModeBadge aparece

Mostra como cada job foi processado:

- Card na lista `/jobs`
- Hero do detalhe `/jobs/[id]`
- Header da revisão `/jobs/[id]/review`

Cores: verde "🤖 OPF" se `opf_used=true`; cinza pontilhado "📋 só regex" se `false`; nada se `null` (legado).

Próximo: [[15 - Testes]].
