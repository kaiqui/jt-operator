# Titlis Operator

> Kubernetes Operator de Observabilidade e Governança — automatiza scorecard de maturidade, gestão de SLOs, compliance e auto-remediação para workloads no cluster.

---

## Índice

1. [O que é o Titlis Operator?](#1-o-que-é-o-titlis-operator)
2. [Arquitetura Geral](#2-arquitetura-geral)
3. [Tech Stack](#3-tech-stack)
4. [Estrutura de Pastas](#4-estrutura-de-pastas)
5. [Fluxos Principais](#5-fluxos-principais)
   - 5.1 [Fluxo do Scorecard](#51-fluxo-do-scorecard)
   - 5.2 [Fluxo de Enriquecimento](#52-fluxo-de-enriquecimento-opcional)
   - 5.3 [Fluxo de Notificação](#53-fluxo-de-notificação)
   - 5.4 [Fluxo de Auto-Remediação](#54-fluxo-de-auto-remediação)
   - 5.5 [Fluxo de SLO](#55-fluxo-de-slo)
6. [Como o Score é Calculado](#6-como-o-score-é-calculado)
   - 6.1 [Visão Geral do Cálculo](#61-visão-geral-do-cálculo)
   - 6.2 [Pilares e Pesos](#62-pilares-e-pesos)
   - 6.3 [Cálculo do Score por Pilar](#63-cálculo-do-score-por-pilar)
   - 6.4 [Cálculo do Score Geral](#64-cálculo-do-score-geral)
   - 6.5 [Tipos de Regras de Validação](#65-tipos-de-regras-de-validação)
   - 6.6 [Regras Padrão (26 regras)](#66-regras-padrão-26-regras)
   - 6.7 [Exemplo Completo de Cálculo](#67-exemplo-completo-de-cálculo)
7. [CRD AppScorecard](#7-crd-appscorecard)
8. [Configuração](#8-configuração)
9. [Integrações Externas](#9-integrações-externas)
10. [Instalação e Deploy](#10-instalação-e-deploy)
11. [Desenvolvimento Local](#11-desenvolvimento-local)
12. [Testes](#12-testes)

---

## 1. O que é o Titlis Operator?

O **Titlis Operator** é um Kubernetes Operator escrito em Python que automatiza:

| Funcionalidade | Descrição |
|---|---|
| **Scorecard de Maturidade** | Avalia cada Deployment/StatefulSet/DaemonSet em 6 pilares (resiliência, segurança, performance, custo, operacional, compliance) e gera uma nota de 0–100 |
| **Gestão de SLOs** | Transforma CRDs `SLOConfig` em SLOs reais no Datadog automaticamente |
| **Notificações Inteligentes** | Envia alertas em batch por namespace no Slack com cooldown configurável |
| **Auto-Remediação** | Cria Pull Requests no GitHub com correções de HPA e resource requests/limits |
| **Enriquecimento** | Agrega dados de ownership (Backstage) e custo (CAST AI) ao scorecard |

O operador roda **dentro do próprio cluster** como um Deployment com leader election para alta disponibilidade.

---

## 2. Arquitetura Geral

O projeto adota uma **Arquitetura em Camadas** inspirada em Hexagonal/Clean Architecture:

```
┌─────────────────────────────────────────────────────────┐
│                    KUBERNETES CLUSTER                    │
│                                                         │
│  Deployment / StatefulSet / DaemonSet   SLOConfig CRD  │
│              │                               │          │
│              ▼                               ▼          │
│  ┌─────────────────────────────────────────────────┐   │
│  │              CONTROLLERS (Kopf Handlers)         │   │
│  │   scorecard_controller.py  │  slo_controller.py  │   │
│  └──────────────┬─────────────────────┬────────────┘   │
│                 │                     │                  │
│                 ▼                     ▼                  │
│  ┌──────────────────┐   ┌──────────────────────────┐   │
│  │  APPLICATION     │   │  APPLICATION             │   │
│  │  SERVICES        │   │  SERVICES                │   │
│  │                  │   │                          │   │
│  │ ScorecardService │   │ SLOService               │   │
│  │ RemediationSvc   │   │ SLOMetricsService        │   │
│  │ ScorecardEnricher│   │                          │   │
│  │ SlackService     │   │                          │   │
│  └──────┬───────────┘   └────────────┬─────────────┘   │
│         │                            │                  │
│         ▼                            ▼                  │
│  ┌─────────────────────────────────────────────────┐   │
│  │              INFRASTRUCTURE (Adapters)           │   │
│  │                                                 │   │
│  │  Kubernetes │ Datadog │ Slack │ GitHub          │   │
│  │  Backstage  │ CAST AI                           │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              DOMAIN (Models)                     │   │
│  │  ValidationRule │ ResourceScorecard │ PillarScore │   │
│  │  SLO │ ComplianceReport │ EnrichedScorecard      │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Padrões de Design utilizados:**

- **Singleton/Factory** — `src/bootstrap/dependencies.py` injeta dependências e garante conexões únicas
- **Adapter** — Cada integração externa (Datadog, Slack, GitHub) fica isolada em `src/infrastructure/`
- **Strategy** — `SLOService` troca a estratégia de geração de queries conforme o `app_framework`
- **Observer** — Kopf observa eventos de Kubernetes e despacha para os handlers corretos

---

## 3. Tech Stack

| Componente | Tecnologia |
|---|---|
| Linguagem | Python 3.12+ |
| Framework de Operator | [Kopf](https://kopf.readthedocs.io/) |
| Cliente Kubernetes | `kubernetes` (client oficial) |
| Validação de Settings | `pydantic` |
| Logs Estruturados | JSON via `src/utils/json_logger.py` |
| Testes | `pytest`, `pytest-asyncio`, `pytest-mock` |
| Gestão de Dependências | `poetry` |
| Integração Datadog | `datadog-api-client` |
| Integração Slack | `slack_sdk` |
| Integração GitHub | `PyGithub` / HTTP client |
| Integração Backstage | HTTP (`requests`) |
| Integração CAST AI | HTTP (`requests`) |

---

## 4. Estrutura de Pastas

```
jt-operator/
├── src/
│   ├── main.py                          # Handlers de startup/cleanup do Kopf
│   ├── settings.py                      # Configurações via variáveis de ambiente (Pydantic)
│   │
│   ├── controllers/                     # Ponto de entrada — Kopf handlers
│   │   ├── base.py                      # BaseController com helpers compartilhados
│   │   ├── scorecard_controller.py      # Avalia Deployments e dispara notificações
│   │   ├── slo_controller.py            # Reconcilia SLOConfig CRDs com Datadog
│   │   └── castai_monitor_controller.py # Monitora recomendações de custo do CAST AI
│   │
│   ├── application/
│   │   ├── ports/                       # Contratos/interfaces das integrações externas
│   │   │   ├── slack_port.py
│   │   │   ├── github_port.py
│   │   │   ├── datadog_port.py
│   │   │   └── status_writer.py
│   │   └── services/                    # Lógica de negócio pura
│   │       ├── scorecard_service.py     # Motor de scorecard — avalia, pontua, cacheia
│   │       ├── scorecard_enricher.py    # Enriquece scorecard com Backstage + CAST AI
│   │       ├── remediation_service.py   # Cria PRs de auto-remediação no GitHub
│   │       ├── slack_service.py         # Abstrai envio de notificações Slack
│   │       ├── namespace_notification_buffer.py  # Buffer de notificações em batch
│   │       ├── slo_service.py           # Lógica de criação/atualização de SLOs
│   │       └── slo_metrics_service.py   # Métricas para SLOs
│   │
│   ├── domain/                          # Modelos de domínio (sem dependências externas)
│   │   ├── models.py                    # ValidationRule, ResourceScorecard, PillarScore, SLO...
│   │   ├── enriched_scorecard.py        # EnrichedScorecard, BackstageProfile, CostProfile
│   │   ├── github_models.py             # RemediationRequest, RemediationResult, PullRequest
│   │   └── slack_models.py              # NotificationSeverity, NotificationChannel
│   │
│   ├── infrastructure/                  # Implementações concretas das integrações
│   │   ├── kubernetes/
│   │   │   ├── client.py               # Inicialização dos clientes K8s
│   │   │   ├── appscorecard_writer.py  # Cria/atualiza CRD AppScorecard
│   │   │   ├── state_store.py          # Persiste estado em ConfigMaps
│   │   │   └── k8s_status_writer.py    # Atualiza .status de CRDs
│   │   ├── datadog/
│   │   │   ├── repository.py           # Operações no Datadog (SLOs, métricas)
│   │   │   ├── client.py               # Client com retry exponencial
│   │   │   └── managers/               # Managers específicos (slo, metrics, castai)
│   │   ├── slack/
│   │   │   ├── repository.py           # Envio de mensagens Slack
│   │   │   └── message_builder.py      # Formatação de mensagens
│   │   ├── github/
│   │   │   ├── client.py               # Client HTTP para GitHub API
│   │   │   └── repository.py           # Operações no GitHub (branches, PRs, commits)
│   │   ├── backstage/
│   │   │   └── enricher.py             # Busca metadata de serviços no Backstage
│   │   └── castai/
│   │       └── cost_enricher.py        # Busca dados de custo do CAST AI
│   │
│   ├── bootstrap/
│   │   └── dependencies.py             # Dependency injection / factory functions
│   │
│   └── utils/
│       ├── json_logger.py              # Logger estruturado em JSON
│       └── logging_bootstrap.py        # Configuração inicial de logging
│
├── config/
│   ├── scorecard-config.yaml           # Configuração customizável de regras
│   └── env-validation-rules.yaml       # Regras de validação de variáveis de ambiente
│
├── charts/                             # Helm chart para deploy no cluster
├── tests/                              # Testes unitários e de integração
│   ├── conftest.py
│   ├── mock_kopf.py
│   ├── unit/
│   └── integration/
│
├── pyproject.toml                      # Definição do projeto e dependências
└── Makefile                            # Comandos úteis de desenvolvimento
```

---

## 5. Fluxos Principais

### 5.1 Fluxo do Scorecard

Disparado a cada evento de **create** ou **update** em qualquer `Deployment`.

```
Deployment criado/atualizado no cluster
           │
           ▼
  @kopf.on.create / @kopf.on.update
  scorecard_controller.on_resource_event()
           │
           ├─ Namespace na lista de exclusão? → ignora
           │
           ▼
  ScorecardService.evaluate_resource(namespace, name, kind)
           │
           ├─ Está no cache (TTL 5 min)? → retorna cache
           │
           ▼
  Busca recurso via Kubernetes AppsV1Api
           │
           ▼
  Filtra regras aplicáveis:
    regras onde enabled=True e kind ∈ rule.applies_to
           │
           ▼
  Para cada regra:
    ├─ Existe _validate_{rule_id}()? → chama método específico
    └─ Senão → _validate_generic()
         ├─ Extrai valor via _extract_value_from_resource()
         │    ├─ Caminho dot-notation (ex: "spec.template.spec.containers[0].image")
         │    └─ Funções callable (HPA, NetworkPolicy, ratio de limits)
         └─ Aplica lógica por tipo:
              BOOLEAN → valor existe?
              NUMERIC → min_value ≤ valor ≤ max_value?
              ENUM    → valor ∈ allowed_values?
              REGEX   → re.match(pattern, valor)?
           │
           ▼
  _calculate_pillar_scores(validation_results)
    Agrupa resultados por pilar
    pillar_score = (soma pesos aprovados / soma total pesos) × 100
           │
           ▼
  _calculate_overall_score(pillar_scores)
    overall = média ponderada dos pillar_scores
    pesos: RESILIENCE 30%, SECURITY 25%, COMPLIANCE 20%,
           PERFORMANCE 15%, OPERATIONAL 10%, COST 10%
           │
           ▼
  Cria ResourceScorecard
    - overall_score, pillar_scores
    - critical_issues, error_issues, warning_issues
    - passed_checks / total_checks
           │
           ├─ Armazena em cache (5 min TTL)
           ├─ store_history=True → persiste histórico no KubeStateStore
           │
           ▼
  ScorecardController:
    ├─ Auto-remediação → _maybe_create_remediation_pr()
    ├─ AppScorecard CRD → appscorecard_writer.upsert()
    └─ Notificação → should_notify()? → buffer → flush → Slack
```

---

### 5.2 Fluxo de Enriquecimento (Opcional)

Agrega dados de ownership e custo ao scorecard base.

```
ResourceScorecard criado
           │
           ▼
  ScorecardEnricher.enrich_and_store(scorecard)
           │
           ▼
  Busca BackstageProfile:
    ├─ Query Backstage catalog por anotação "kubernetes-id"
    ├─ Fallback: busca por nome do serviço
    └─ Fallback final: BackstageProfile.unknown()
           │
           Retorna: owner, squad, tier, slo_target_override,
                    scorecard_enabled, tech_lead_email
           │
           ▼
  Se backstage.scorecard_enabled = true:
    Busca CostProfile no CAST AI:
      ├─ GET /v1/cost/workloads → monthly_cost, savings
      ├─ GET /v1/recommendations → rightsizing suggestions
      └─ Fallback: CostProfile.unavailable()
           │
           Retorna: monthly_cost_usd, cpu_efficiency_pct,
                    memory_efficiency_pct, waste_usd
           │
           ▼
  Cria EnrichedScorecard { scorecard, backstage, cost }
    Métrica calculada: cost_per_score_point = monthly_cost / overall_score
           │
           ▼
  ScorecardsStore.upsert(enriched)  ← in-memory store
           │
           ▼
  API summary disponível:
    - por squad: get_by_squad(squad)
    - geral: platform_summary()
```

---

### 5.3 Fluxo de Notificação

Sistema de batch por namespace com cooldown e digest.

```
ResourceScorecard avaliado
           │
           ▼
  ScorecardService.should_notify(scorecard):
    ├─ Cooldown ativo? (padrão 60 min) → NÃO notifica
    ├─ overall_score < notify_critical_threshold (70)? → SIM
    ├─ critical_issues > 0? → SIM
    ├─ error_issues > 3? → SIM
    ├─ score < notify_error_threshold (80) e error_issues > 0? → SIM
    ├─ score < notify_warning_threshold (90) e warning_issues > 5? → SIM
    └─ Senão → NÃO notifica
           │
           ▼ (se deve notificar)
  NamespaceNotificationBuffer.add_and_maybe_flush(scorecard):
    ├─ Adiciona scorecard ao buffer do namespace
    ├─ Intervalo desde último flush > 15 min? → retorna lista para flush
    └─ Senão → retorna None (continua bufferizando)
           │
           ▼ (se flush)
  _send_namespace_digest(namespace, scorecards):
    │
    ├─ _format_namespace_digest():
    │    ├─ Ordena: piores scores primeiro (críticos > erros > score)
    │    ├─ Determina severidade geral: 🔴 crítico / 🟠 erro / 🟡 warning / 🟢 ok
    │    ├─ Monta tabela por app: nome | score | issues
    │    ├─ Lista top 5 findings críticos/erros
    │    └─ Adiciona hint kubectl
    │
    ▼
  SlackNotificationService.send():
    ├─ Rate limit: 60 req/min, 360 req/hora
    ├─ Envia via WebHook ou Bot Token
    └─ Mensagem truncada em 3000 chars (limite Slack)
           │
           ▼
  AppScorecardWriter.update_notification():
    └─ Atualiza metadata de notificação no CRD
```

**Escalas de score para emoji:**

| Score | Emoji | Status |
|---|---|---|
| ≥ 90 | 🟢 | Excelente |
| ≥ 80 | 🟡 | Bom |
| ≥ 70 | 🟠 | Regular |
| < 70 | 🔴 | Crítico |

---

### 5.4 Fluxo de Auto-Remediação

Cria Pull Requests automáticos no GitHub para corrigir issues remediáveis.

```
ResourceScorecard com issues remediáveis
  (regras: RES-007, RES-008, PERF-002, RES-003 a RES-006)
           │
           ▼
  ScorecardController._maybe_create_remediation_pr()
    └─ Coleta RemediationIssue para cada regra remediável que falhou
           │
           ▼
  RemediationService.create_remediation_pr(RemediationRequest):
    │
    ├─ 1. Extrai repo do env DD_GIT_REPOSITORY_URL
    ├─ 2. Verifica lock na memória (evita concorrência)
    ├─ 3. Verifica se PR já existe no GitHub (evita duplicatas)
    ├─ 4. Adquire lock
    ├─ 5. (Opcional) Busca métricas reais no Datadog para sugestões
    │
    ├─ 6. Lê deploy.yaml do repositório GitHub
    │
    ├─ 7. Modifica deploy.yaml com ruamel.yaml:
    │      ├─ Atualiza resources.requests e resources.limits
    │      │    (usa métricas reais ou valores padrão conservadores)
    │      └─ Cria/atualiza HPA manifest
    │
    ├─ 8. Cria branch: titlis/remediation/{namespace}-{name}-{timestamp}
    ├─ 9. Commita deploy.yaml modificado
    ├─ 10. Cria Pull Request com descrição detalhada dos fixes
    ├─ 11. Notifica Slack com link do PR
    └─ 12. Libera lock
           │
           ▼
  RemediationResult { success, pull_request, branch_name }
           │
           ▼
  AppScorecard CRD atualizado com:
    - prNumber, prUrl, prBranch
    - status: "open"
    - issuesFixed: [lista de rule_ids corrigidos]
```

---

### 5.5 Fluxo de SLO

Gerencia SLOs no Datadog a partir de CRDs `SLOConfig`.

```
SLOConfig CRD criado/atualizado
           │
           ▼
  @kopf.on.create / @kopf.on.update (slo_controller.py)
           │
           ▼
  Validações iniciais:
    ├─ spec.service existe?
    └─ warning_threshold > target_threshold?
           │
           ▼
  SLOService._build_slo_from_spec(spec):
    ├─ app_framework = "fastapi"  → gera queries FastAPI automaticamente
    ├─ app_framework = "wsgi"     → gera queries WSGI
    ├─ app_framework = "aiohttp"  → gera queries AIOHTTP
    └─ numerator/denominator definidos → usa queries customizadas
           │
           ▼
  DatadogRepository.find_slo_by_tag("slo_uid:{uid}"):
    ├─ Encontrou SLO existente:
    │    └─ Há diferença? → DatadogRepository.update_slo()
    └─ Não encontrou:
         └─ DatadogRepository.create_slo()
           │
           ▼
  K8sStatusWriter.update_status(CRD):
    ├─ Sucesso: status.slo_id, status.state = "synced"
    └─ Erro: status.error, status.state = "error"
```

---

## 6. Como o Score é Calculado

### 6.1 Visão Geral do Cálculo

O score segue uma hierarquia de três níveis:

```
┌─────────────────────────────────────────────────────────┐
│  NÍVEL 1: Regras de Validação                           │
│                                                         │
│  Cada regra tem um PESO e retorna APROVADO ou REPROVADO │
│  Exemplo: RES-001 (Liveness Probe) peso=10, REPROVADO   │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  NÍVEL 2: Score por Pilar                               │
│                                                         │
│  pillar_score = (soma pesos aprovados / soma total) × 100│
│  Exemplo: RESILIENCE = (52 / 88) × 100 = 59.1          │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  NÍVEL 3: Score Geral                                   │
│                                                         │
│  overall = média ponderada dos scores de pilar          │
│  Exemplo: 30%×59.1 + 25%×75 + ... = 68.2              │
└─────────────────────────────────────────────────────────┘
```

---

### 6.2 Pilares e Pesos

O score geral é a **média ponderada** dos scores de cada pilar:

| Pilar | Peso | Foco |
|---|---|---|
| **RESILIENCE** | **30%** | Probes, recursos, HPA, réplicas, rollout |
| **SECURITY** | **25%** | Imagem, filesystem, privilege escalation, capabilities |
| **COMPLIANCE** | **20%** | Conformidade com padrões internos |
| **PERFORMANCE** | **15%** | Ratio limits/requests, target HPA |
| **OPERATIONAL** | **10%** | Operabilidade, manutenção |
| **COST** | **10%** | Otimização de custos |

> **Nota:** Pilares sem regras configuradas não entram no cálculo. O peso é redistribuído proporcionalmente entre os pilares presentes.

---

### 6.3 Cálculo do Score por Pilar

```python
# Para cada pilar:
total_weight  = sum(rule.weight  for rule in pillar_rules)
passed_weight = sum(rule.weight  for rule in pillar_rules if rule.passed)

pillar_score = (passed_weight / total_weight) * 100
```

**Exemplo — Pilar RESILIENCE:**

| Regra | Peso | Status |
|---|---|---|
| RES-001 Liveness Probe | 10 | ✅ APROVADO |
| RES-002 Readiness Probe | 10 | ✅ APROVADO |
| RES-003 CPU Requests | 8 | ✅ APROVADO |
| RES-004 CPU Limits | 5 | ❌ REPROVADO |
| RES-005 Memory Requests | 8 | ✅ APROVADO |
| RES-006 Memory Limits | 5 | ❌ REPROVADO |
| RES-007 HPA Configurado | 7 | ❌ REPROVADO |
| RES-008 HPA com Métricas | 5 | ❌ REPROVADO |
| RES-009 Graceful Shutdown | 3 | ✅ APROVADO |
| RES-010 Container Non-Root | 10 | ✅ APROVADO |
| RES-011 Pod Security Context | 5 | ✅ APROVADO |
| RES-012 NetworkPolicy | 7 | ❌ REPROVADO |
| RES-013 Min 2 Réplicas | 6 | ✅ APROVADO |
| RES-014 Estratégia Rollout | 4 | ✅ APROVADO |
| **TOTAL** | **93** | |

```
passed_weight = 10+10+8+8+3+10+5+6+4 = 64
total_weight  = 93
pillar_score  = (64 / 93) × 100 = 68.8
```

---

### 6.4 Cálculo do Score Geral

```python
pillar_weights = {
    ValidationPillar.RESILIENCE:  30.0,
    ValidationPillar.SECURITY:    25.0,
    ValidationPillar.COMPLIANCE:  20.0,
    ValidationPillar.PERFORMANCE: 15.0,
    ValidationPillar.OPERATIONAL: 10.0,
    ValidationPillar.COST:        10.0,
}

total_weight  = sum(pillar_weights.values())  # 110.0
weighted_sum  = sum(pillar_score × weight for each pillar)
overall_score = weighted_sum / total_weight
```

**Continuando o exemplo:**

| Pilar | Score do Pilar | Peso | Contribuição |
|---|---|---|---|
| RESILIENCE | 68.8 | 30 | 2.064 |
| SECURITY | 89.3 | 25 | 2.232 |
| PERFORMANCE | 71.4 | 15 | 1.071 |
| COMPLIANCE | 100.0 | 20 | 2.000 |
| OPERATIONAL | 100.0 | 10 | 1.000 |
| COST | 100.0 | 10 | 1.000 |
| **TOTAL** | | **110** | **9.367** |

```
overall_score = 9.367 / 110 × 100 = 85.2
```

> Score de **85.2** → Bom 🟡 (≥80, <90)

---

### 6.5 Tipos de Regras de Validação

#### `BOOLEAN` — Existência de configuração

Passa se o valor **existe e não é nulo**.

```python
# Exemplo: RES-001 - Liveness Probe
path = "spec.template.spec.containers[0].livenessProbe"
value = extract(path)  # retorna o objeto probe ou None
passed = (value is not None)
```

#### `NUMERIC` — Valor numérico dentro de faixa

Passa se `min_value ≤ valor ≤ max_value`.

```python
# Exemplo: RES-013 - Mínimo 2 réplicas
path = "spec.replicas"
value = extract(path)  # retorna 1
passed = (value >= 2)  # False — apenas 1 réplica
```

Suporte a unidades Kubernetes:
- `"100m"` → 0.1 (CPU em cores)
- `"512Mi"` → 512 (memória em MiB)
- `"2Gi"` → 2048 (memória em MiB)

#### `ENUM` — Valor em lista permitida

Passa se `valor ∈ allowed_values`.

```python
# Exemplo hipotético: ambiente permitido
allowed_values = ["production", "staging"]
value = "development"
passed = (value in allowed_values)  # False
```

#### `REGEX` — Valor corresponde a padrão

Passa se `re.match(pattern, valor)` retornar match.

```python
# Exemplo: SEC-001 - Imagem sem :latest
pattern = r"^(?!.*:latest$).+"
value = "myapp:1.2.3"   # passa
value = "myapp:latest"  # não passa
```

---

### 6.6 Regras Padrão (26 regras)

#### Pilar RESILIENCE (14 regras)

| ID | Nome | Tipo | Peso | Severidade |
|---|---|---|---|---|
| RES-001 | Liveness Probe Configurada | BOOLEAN | 10.0 | ERROR |
| RES-002 | Readiness Probe Configurada | BOOLEAN | 10.0 | ERROR |
| RES-003 | CPU Requests Definidos | BOOLEAN | 8.0 | ERROR |
| RES-004 | CPU Limits Definidos | BOOLEAN | 5.0 | WARNING |
| RES-005 | Memory Requests Definidos | BOOLEAN | 8.0 | ERROR |
| RES-006 | Memory Limits Definidos | BOOLEAN | 5.0 | WARNING |
| RES-007 | HPA Configurado | BOOLEAN | 7.0 | WARNING |
| RES-008 | HPA com Métricas | BOOLEAN | 5.0 | WARNING |
| RES-009 | Graceful Shutdown Configurado | BOOLEAN | 3.0 | INFO |
| RES-010 | Container Non-Root | BOOLEAN | 10.0 | ERROR |
| RES-011 | Pod Security Context | BOOLEAN | 5.0 | WARNING |
| RES-012 | NetworkPolicy Aplicada | BOOLEAN | 7.0 | WARNING |
| RES-013 | Mínimo 2 Réplicas | NUMERIC (min=2) | 6.0 | WARNING |
| RES-014 | Estratégia de Rollout | BOOLEAN | 4.0 | WARNING |

#### Pilar SECURITY (4 regras)

| ID | Nome | Tipo | Peso | Severidade |
|---|---|---|---|---|
| SEC-001 | Imagem com Tag Específica | REGEX | 9.0 | ERROR |
| SEC-002 | ReadOnly Root Filesystem | BOOLEAN | 6.0 | WARNING |
| SEC-003 | Privilege Escalation Desabilitado | BOOLEAN | 8.0 | ERROR |
| SEC-004 | Capabilities Reduzidas | BOOLEAN | 5.0 | WARNING |

#### Pilar PERFORMANCE (2 regras)

| ID | Nome | Tipo | Peso | Severidade |
|---|---|---|---|---|
| PERF-001 | Resource Limits Adequados | NUMERIC (max=3.0) | 4.0 | WARNING |
| PERF-002 | HPA com Target Adequado | NUMERIC (50–90) | 3.0 | INFO |

> **Nota:** Os pilares COMPLIANCE, OPERATIONAL e COST não possuem regras padrão — são calculados a partir de regras customizadas adicionadas via `scorecard-config.yaml`.

---

### 6.7 Exemplo Completo de Cálculo

**Deployment `payment-api` no namespace `production`:**

```yaml
# Configuração atual (problemática)
spec:
  replicas: 1                          # ❌ RES-013: menos de 2 réplicas
  template:
    spec:
      containers:
      - name: app
        image: myrepo/payment:latest   # ❌ SEC-001: tag :latest
        resources:
          requests:
            cpu: "100m"                # ✅ RES-003
            memory: "128Mi"            # ✅ RES-005
          # sem limits                 # ❌ RES-004, RES-006
        livenessProbe: {}              # ✅ RES-001
        readinessProbe: {}             # ✅ RES-002
        securityContext:
          runAsNonRoot: true           # ✅ RES-010
          # sem readOnlyRootFilesystem # ❌ SEC-002
          # sem allowPrivilegeEscalation: false  # ❌ SEC-003
      terminationGracePeriodSeconds: 30  # ✅ RES-009
      securityContext: {}             # ✅ RES-011
      # sem strategy                  # ❌ RES-014
# sem HPA                             # ❌ RES-007, RES-008
# sem NetworkPolicy                   # ❌ RES-012
```

**Cálculo do score RESILIENCE:**

| Regra | Aprovado? | Peso contribuído |
|---|---|---|
| RES-001 Liveness Probe | ✅ | 10 |
| RES-002 Readiness Probe | ✅ | 10 |
| RES-003 CPU Requests | ✅ | 8 |
| RES-004 CPU Limits | ❌ | 0 |
| RES-005 Memory Requests | ✅ | 8 |
| RES-006 Memory Limits | ❌ | 0 |
| RES-007 HPA | ❌ | 0 |
| RES-008 HPA Métricas | ❌ | 0 |
| RES-009 Graceful Shutdown | ✅ | 3 |
| RES-010 Non-Root | ✅ | 10 |
| RES-011 Security Context | ✅ | 5 |
| RES-012 NetworkPolicy | ❌ | 0 |
| RES-013 Min 2 Réplicas | ❌ | 0 |
| RES-014 Rollout Strategy | ❌ | 0 |
| **Score RESILIENCE** | | **(54/93)×100 = 58.1** |

**Cálculo do score SECURITY:**

| Regra | Aprovado? | Peso contribuído |
|---|---|---|
| SEC-001 Tag Específica | ❌ | 0 |
| SEC-002 ReadOnly FS | ❌ | 0 |
| SEC-003 No Priv Escalation | ❌ | 0 |
| SEC-004 Capabilities | ❌ | 0 |
| **Score SECURITY** | | **(0/28)×100 = 0.0** |

**Score Geral:**

```
overall = (58.1×30 + 0.0×25 + 0.0×15 + 0.0×20 + 0.0×10 + 0.0×10) / 110
        = 1743 / 110
        = 15.8
```

**Resultado:** Score **15.8 / 100** — 🔴 Crítico

**Issues contadas:**
- `critical_issues = 0`
- `error_issues = 5` (RES-003¹, SEC-001, SEC-003, RES-001¹, RES-002¹... os que falharam com severity ERROR)
- `warning_issues = 7`

**Notificação disparada?** Sim — `overall_score (15.8) < notify_critical_threshold (70.0)`

---

## 7. CRD AppScorecard

O operador cria/atualiza automaticamente um CRD `AppScorecard` para cada Deployment avaliado:

```yaml
apiVersion: titlis.io/v1
kind: AppScorecard
metadata:
  name: payment-api
  namespace: production
  ownerReferences:
    - apiVersion: apps/v1
      kind: Deployment
      name: payment-api          # Auto garbage-collect quando Deployment é deletado
spec: {}
status:
  overallScore: 15.8
  pillarScores:
    resilience:
      score: 58.1
      passedChecks: 6
      totalChecks: 14
    security:
      score: 0.0
      passedChecks: 0
      totalChecks: 4
    performance:
      score: 0.0
      passedChecks: 0
      totalChecks: 2
  issues:
    critical: 0
    errors: 5
    warnings: 7
  findings:
    - ruleId: SEC-001
      ruleName: "Imagem com Tag Específica"
      severity: error
      message: "❌ Valor myrepo/payment:latest não corresponde ao padrão"
      remediation: "Use tags versionadas (ex: v1.2.3) ao invés de 'latest'"
    - ruleId: RES-007
      ruleName: "HPA Configurado"
      severity: warning
      message: "❌ HPA não encontrado"
      remediation: "Configure HPA para auto-scaling baseado em demanda"
  remediationPR:
    prNumber: 142
    prUrl: "https://github.com/org/repo/pull/142"
    prBranch: "titlis/remediation/production-payment-api-20240115"
    status: "open"
    issuesFixed: ["RES-007", "RES-003", "RES-004"]
  lastEvaluated: "2024-01-15T10:30:00Z"
```

**Comandos úteis:**

```bash
# Listar todos os AppScorecards do cluster
kubectl get appscorecard -A

# Ver scorecard de um deployment específico
kubectl get appscorecard payment-api -n production -o yaml

# Ver apenas os scores
kubectl get appscorecard -n production \
  -o custom-columns='NAME:.metadata.name,SCORE:.status.overallScore,ERRORS:.status.issues.errors'
```

---

## 8. Configuração

### 8.1 Variáveis de Ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `ENABLE_SCORECARD_CONTROLLER` | `true` | Habilita o scorecard |
| `ENABLE_SLO_CONTROLLER` | `true` | Habilita gestão de SLOs |
| `ENABLE_AUTO_REMEDIATION` | `true` | Habilita criação de PRs |
| `ENABLE_BACKSTAGE_ENRICHMENT` | `false` | Enriquecimento com Backstage |
| `ENABLE_CASTAI_COST_ENRICHMENT` | `false` | Enriquecimento com CAST AI |
| `SLACK_ENABLED` | `true` | Habilita notificações Slack |
| `SLACK_BOT_TOKEN` | — | Token do bot Slack |
| `SLACK_DEFAULT_CHANNEL` | `#titlis-notifications` | Canal padrão |
| `GITHUB_TOKEN` | — | Token do GitHub |
| `GITHUB_BASE_BRANCH` | `develop` | Branch base para PRs |
| `BACKSTAGE_URL` | — | URL da instância Backstage |
| `BACKSTAGE_TOKEN` | — | Token de autenticação Backstage |
| `CASTAI_API_KEY` | — | API key do CAST AI |
| `CASTAI_CLUSTER_ID` | — | ID do cluster no CAST AI |

### 8.2 Configuração do Scorecard (`config/scorecard-config.yaml`)

```yaml
# Ajustar pesos e severidades das regras padrão
rules:
  - id: "RES-001"
    enabled: true
    weight: 15.0          # Aumenta peso (era 10.0)
    severity: "critical"  # Eleva severidade

  - id: "RES-007"
    enabled: false         # Desabilita verificação de HPA

# Adicionar regras customizadas
  - id: "COMP-001"
    name: "Label 'team' obrigatório"
    pillar: "compliance"
    type: "boolean"
    severity: "error"
    weight: 8.0
    applies_to: ["Deployment", "StatefulSet"]
    description: "Todo workload deve ter o label 'team' definido"
    remediation: "Adicione 'labels.team: nome-do-time' no metadata"

# Thresholds de notificação
notification_thresholds:
  critical: 65.0   # Abaixo disso → notificação crítica
  error: 75.0      # Abaixo disso → notificação de erro
  warning: 85.0    # Abaixo disso → notificação de warning

notification_settings:
  cooldown_minutes: 120   # Intervalo entre notificações do mesmo recurso
  batch: true             # Agrupa por namespace
  batch_interval: 30      # Flush a cada 30 min

# Namespaces ignorados
excluded_namespaces:
  - "kube-system"
  - "kube-public"
  - "monitoring"
  - "datadog"

advanced:
  store_history: true
  max_history_per_resource: 20
  enable_drift_detection: true
```

---

## 9. Integrações Externas

| Integração | Uso | Fallback |
|---|---|---|
| **Kubernetes API** | Leitura de Deployments, HPAs, NetworkPolicies; escrita de AppScorecard CRDs | Obrigatório |
| **Slack** | Notificações de scorecard e digest por namespace | Silencioso (log) |
| **GitHub** | Criação de PRs de auto-remediação | Silencioso (log) |
| **Datadog** | Métricas para sugestões de resources; gestão de SLOs | Usa valores padrão |
| **Backstage** | Ownership, squad, tier, SLO overrides | `BackstageProfile.unknown()` |
| **CAST AI** | Custo mensal, eficiência CPU/memória, rightsizing | `CostProfile.unavailable()` |

Todas as integrações externas têm **graceful degradation** — uma falha de integração não interrompe o loop principal do operador.

---

## 10. Instalação e Deploy

### Via Helm

```bash
# Adicionar repositório (ajustar conforme configuração)
helm install titlis-operator ./charts/titlis-operator \
  --namespace titlis-system \
  --create-namespace \
  --set slack.botToken="xoxb-..." \
  --set github.token="ghp_..." \
  --set scorecard.enabled=true
```

### Via kubectl

```bash
# Aplicar CRDs
kubectl apply -f charts/titlis-operator/crds/

# Criar namespace e secrets
kubectl create namespace titlis-system
kubectl create secret generic titlis-secrets \
  --from-literal=SLACK_BOT_TOKEN="xoxb-..." \
  --from-literal=GITHUB_TOKEN="ghp_..." \
  -n titlis-system

# Aplicar ConfigMap de configuração
kubectl apply -f config/scorecard-config.yaml -n titlis-system

# Aplicar Deployment do operador
kubectl apply -f charts/titlis-operator/templates/ -n titlis-system
```

---

## 11. Desenvolvimento Local

```bash
# Instalar dependências
poetry install

# Configurar variáveis de ambiente
cp .env.example .env
# editar .env conforme necessário

# Rodar operator localmente (aponta para cluster atual via ~/.kube/config)
poetry run kopf run src/main.py --dev

# Rodar sem conectar ao cluster real (modo simulado)
TITLIS_DRY_RUN=true poetry run kopf run src/main.py --dev
```

**Logs estruturados:**

```bash
# Seguir logs do operator no cluster
kubectl logs -n titlis-system -l app=titlis-operator -f | jq '.'

# Filtrar por recurso específico
kubectl logs -n titlis-system -l app=titlis-operator -f | \
  jq 'select(.resource_name == "payment-api")'
```

---

## 12. Testes

```bash
# Todos os testes
poetry run pytest

# Apenas unitários
poetry run pytest tests/unit/ -v

# Apenas integração
poetry run pytest tests/integration/ -v

# Com cobertura
poetry run pytest --cov=src --cov-report=html

# Teste específico
poetry run pytest tests/unit/test_domain_models.py -k "test_pillar_score" -v
```

**Estrutura de testes:**

```
tests/
├── conftest.py                      # Fixtures compartilhadas e mocks
├── mock_kopf.py                     # Mock do framework Kopf
├── unit/
│   ├── test_domain_models.py        # Testa modelos de domínio
│   ├── test_controllers.py          # Testa controllers com mocks
│   ├── test_remediation_service.py  # Testa auto-remediação
│   ├── test_datadog.py              # Testa integração Datadog
│   └── test_github_repository.py   # Testa integração GitHub
└── integration/
    ├── test_mocked_datadog.py       # Integração completa com Datadog mockado
    └── test_mocked_slack.py         # Integração completa com Slack mockado
```

---

## Documentação Adicional

- [`docs/guia-extensao-scorecard.md`](docs/guia-extensao-scorecard.md) — Guia completo para adicionar novas validações ao scorecard
- [`usege.crd.example.yaml`](usege.crd.example.yaml) — Exemplo de uso do CRD AppScorecard
- [`config/scorecard-config.yaml`](config/scorecard-config.yaml) — Configuração das regras de validação

---

*Titlis Operator — Observability & Governance Automation*
