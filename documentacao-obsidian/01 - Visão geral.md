---
tags: [lgpdoc, visão-geral]
---

# Visão geral

LGPDoc é um pipeline **local** de anonimização e pseudonimização de documentos brasileiros. Lê arquivos comuns de escritório, detecta dados pessoais (PII) e produz uma versão segura do documento — pronta para análise externa, processamento por LLM ou compartilhamento.

## Para quem é

- **Advocacia / perícia / contabilidade** preparando documentos para envio ou análise externa.
- **Times pequenos** que precisam controlar reidentificação de dados sem depender de SaaS.
- **Profissionais autônomos** que rodam a ferramenta numa máquina só.

## O que faz, em uma linha

> Recebe `.pdf`/`.docx`/`.xlsx`/etc. → detecta PII → produz texto redigido + tabela de marcadores → permite revisão humana → libera download.

## Como faz

Duas camadas de detecção, complementares:

1. **OpenAI Privacy Filter (OPF)** — modelo de linguagem treinado para identificar PII. Pega nomes em narrativa e contextos sutis. Opcional — vê [[06 - OPF runtime toggle]].
2. **Regras determinísticas brasileiras** — 30+ regex específicas pra documentos do Brasil. Vê [[05 - Detectores]].

A saída pode ser **anonimizada** (substituições irreversíveis) ou **pseudonimizada reversível** (marcadores indexados que podem ser restaurados). Vê [[07 - Modos de processamento]].

## Princípios de design

- **Local-first** — nenhum byte de documento sai da máquina. Único tráfego externo: download único do modelo OPF (~3 GB) na primeira execução, opcional.
- **Privacidade nos logs** — só metadados (`job_id`, posições, hashes). Conteúdo nunca aparece. Vê [[13 - Privacidade]].
- **Revisão humana é obrigatória** — todo job processado vai para `awaiting_review`. `risk_level` e `decision` são apenas sinais visuais, não bypass.
- **Reversibilidade explícita** — a pseudonimização reversível é um modo, não default. Não dá para ativá-la depois.

## O que NÃO faz

- Não treina modelos próprios — usa OPF como dado.
- Não roda em SaaS — não tem multi-tenant, autenticação, ou TLS gerenciado.
- Não tenta substituir um DPO. Detecta PII conhecida; o trabalho de classificação ainda é humano.

## Stack resumida

- **Python 3.11+** — FastAPI, SQLAlchemy 2.0, Pydantic 2.
- **Next.js 14** — React 18, TypeScript, CSS plain.
- **OpenAI Privacy Filter** (opcional) — torch + transformers em subprocesso isolado.
- **Tesseract + Poppler** — OCR para PDFs escaneados e imagens.

Detalhes em [[03 - Arquitetura]] e [[02 - Instalação]].
