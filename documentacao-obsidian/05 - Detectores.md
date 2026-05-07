---
tags: [lgpdoc, detectores, regex, opf]
---

# Detectores

Duas famílias, complementares.

## Família A — OpenAI Privacy Filter

Modelo de linguagem treinado para identificar PII em texto livre. Pega o que regex não pega: nomes em narrativa, contextos sutis, ALL-CAPS, ambiguidade.

- Implementação: `OpenAIPrivacyFilterClient` em `src/anonymizer/privacy_filter_client.py`.
- Roda em [[06 - OPF runtime toggle|subprocesso isolado]] — dependências (`opf`, `torch`, `transformers`) ficam fora do processo principal.
- Tipos que produz: `private_person`, `private_email`, `private_phone`, `private_address`, `private_date`, `account_number`, etc.

> [!info] Sem OPF, qual o impacto?
> Você perde principalmente detecção de **nome em narrativa sem rótulo** ("Carlos confirmou o envio"). Nomes precedidos de gatilhos brasileiros (Cliente, Sr., Auditor, Diretora...) ainda são detectados pelo regex BR. Vê `detect_br_labeled_names` abaixo.

## Família B — Regex determinísticos brasileiros

Stack em `src/anonymizer/augmentations.py` + `src/anonymizer/regex_detectors.py`.

### Detectores auxiliares (em `augmentations.py`)

| Função | O que detecta |
|---|---|
| `detect_br_labeled_names` | Nomes precedidos de label (`Cliente:`, `Sr.`, `Auditor`, `Diretora`...). Lista de ~50 labels com qualifiers (`Servidor Público Federal`, `Auditor Fiscal`...). |
| `detect_cpfs` | CPF com **validação de dígito verificador**. Sem keyword exigida — DV é forte o bastante. |
| `detect_cnpjs` | CNPJ com **validação de DV**. Idem. |
| `detect_endereco_logradouro` | "Rua/Av/Travessa X, n. Y" |
| `detect_endereco_unidade` | "ap. Z, bloco W" |

> [!warning] Honoríficos com período opcional
> `Sr.` e `Sr` ambos casam (regex usa `\.?`). Para evitar falsos positivos com siglas tipo "AC SR", o body do nome exige ≥2 tokens válidos depois do label.

### Detectores em `REGEX_DETECTORS` (regex_detectors.py)

Cada um produz `entity_type` próprio para que a UI consiga filtrar/explicar.

| Chave | Detecta |
|---|---|
| `rg` | RG com gatilho ("RG: 12.345.678-9") |
| `cnh` | CNH com gatilho |
| `passaporte` | passaporte BR |
| `titulo_eleitor` | título eleitoral |
| `pis` | PIS/PASEP/NIT |
| `ctps` | CTPS |
| `sus` | cartão SUS |
| `oab` | OAB/UF |
| `crm` | CRM/UF |
| `crea` | CREA/UF |
| `placa` | placa de veículo (Mercosul + antiga) |
| `renavam` | RENAVAM |
| `processo_cnj` | processo unificado CNJ |
| `inscricao_estadual` | IE com gatilho |
| `ip` | IPv4 |
| `cep` | CEP |
| `financeiro` | dados bancários (agência, conta) |
| `private_company__suffix` | empresas com sufixo (`Ltda`, `S.A.`, `EIRELI`, `ME`) |
| `private_company__gov` | órgãos públicos (TJ-, MPF-, etc) |
| `private_company__edu` | instituições de ensino |
| `private_date` | datas em PT-BR ("25 de dezembro de 2024", "25/12/2024") |

> [!warning] Detectores genéricos exigem keyword
> RG, CNH, RENAVAM, SUS, IE: o número sozinho é só um digit-blob. A regex exige a palavra-gatilho ("RG: ", "CNH ", etc.) para evitar falsos positivos com numerações genéricas. **Não afrouxe sem mensurar custo de FP.**

## Composição em runtime

`make_augmented_client(base, get_enabled_kinds=...)` monta a stack:

```
1. CaseNormalizingClient envolve o base — title-case ALL-CAPS para o modelo ver "Joao" e não "JOAO".
2. Composite encadeia primary (base) + aux detectors.
3. _OverridingComposite filtra por kinds habilitados em runtime ([[10 - Configurações|/settings]]) e aplica a regra "CPF/CNPJ vencem account_number".
```

## Quando o regex BR roda mas o OPF não

**Sempre que o usuário desliga o OPF** (vê [[06 - OPF runtime toggle]]). O `ToggledBaseClient` despacha para o `RegexFallbackClient` (apenas e-mail, conservador) ou para o subprocesso OPF. As **augmentações regex BR continuam sempre rodando**, independente do toggle — elas são camada acima do base.

Exemplo prático:

| Texto | OPF on | OPF off |
|---|---|---|
| `Cliente: João Silva` | ✅ (label + modelo) | ✅ (label) |
| `JOÃO SILVA assinou` | ✅ (modelo + case-norm) | ❌ (sem label) |
| `Carlos confirmou o envio.` | ✅ (modelo) | ❌ (sem label) |
| `Sr. Carlos Souza` | ✅ | ✅ (label honorific) |
| CPF `123.456.789-09` | ✅ (DV) | ✅ (DV) |
| CNPJ válido | ✅ (DV) | ✅ (DV) |
| `alice@example.com` | ✅ | ✅ (RegexFallback) |
| `OAB/SP 12345` | ✅ | ✅ (regex) |

## Para adicionar um novo detector regex

1. Em `regex_detectors.py`: criar `def detect_X(text: str) -> list[DetectedSpan]:`.
2. Registrar em `REGEX_DETECTORS["x"] = detect_x`.
3. Atualizar `policies/default.yaml` com `entity_type` e estratégia.
4. Adicionar testes em `tests/test_regex_detectors.py` cobrindo caminho feliz + ≥1 FP esperado.

Próximo: [[06 - OPF runtime toggle]].
