---
tags: [lgpdoc, índice]
---

# LGPDoc — Wiki

Documentação técnica do projeto LGPDoc, organizada como vault do Obsidian. Cada nota cobre um domínio do produto e linka para os tópicos relacionados via `[[wikilinks]]`.

> [!info] Versão
> Esta wiki reflete o estado do código em `main` na data desta nota. Para verificar contagens precisas de testes ou LOC, rode `pytest -q` e `wc -l` localmente.

## Por onde começar

- Novo no projeto? → [[01 - Visão geral]]
- Vai instalar? → [[02 - Instalação]]
- Quer entender o fluxo end-to-end? → [[04 - Pipeline de detecção]]
- Quer entender por que o botão OPF existe? → [[06 - OPF runtime toggle]]

## Sumário

### Conceitos

- [[01 - Visão geral]]
- [[03 - Arquitetura]]
- [[07 - Modos de processamento]]
- [[Glossário]]

### Funcionalidades

- [[04 - Pipeline de detecção]]
- [[05 - Detectores]]
- [[06 - OPF runtime toggle]]
- [[08 - Containers]]
- [[09 - Modo de comparação]]
- [[10 - Configurações]]

### Operação

- [[02 - Instalação]]
- [[11 - API]]
- [[12 - Banco de dados]]
- [[13 - Privacidade]]

### Para quem mexe no código

- [[14 - Frontend]]
- [[15 - Testes]]
- [[16 - Desenvolvimento]]

## Convenções desta wiki

- **PT-BR** no texto, **inglês** em nomes de função/classe/arquivo (espelha o código).
- Caminhos de arquivo sempre relativos à raiz do repositório.
- Trechos de código curtos e citáveis. Para olhar o código real, abra o arquivo apontado.
- Callouts `> [!warning]` marcam armadilhas que já causaram bug em algum momento.
