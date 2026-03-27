# CLAUDE.md — Titlis Operator

> Instrução obrigatória: **Após cada alteração no código, execute lint e testes:**
> ```bash
> make lint && make test-unit
> ```
> Nunca entregue código sem confirmar que lint e testes passam.
>
> **Lint padrão: flake8** — é o único linter de estilo a ser respeitado como gate obrigatório.
> Configuração em `.flake8` na raiz do projeto.
>
> **Regra de docstrings: proibido usar docstrings** (D100–D107 ignorados no flake8).
> Não adicione docstrings de módulo, classe ou função. O código deve ser autoexplicativo.

---

## 1. Visão Geral da Arquitetura

O **Titlis Operator** é um Kubernetes Operator escrito em Python (Kopf) que automatiza governança, compliance e remediação inteligente de workloads em clusters Kubernetes.

### Pilares Funcionais

```
┌─────────────────────────────────────────────────────────┐
│                    Titlis Operator                       │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Scorecard   │  │     SLO      │  │ Auto-Remedia-│  │
│  │  Controller  │  │  Controller  │  │    tion       │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │          │
│  ┌──────▼─────────────────▼──────────────────▼───────┐  │
│  │              Application Services                  │  │
│  │  ScorecardService │ SLOService │ RemediationService│  │
│  └──────┬─────────────────┬──────────────────┬───────┘  │
│         │                 │                  │          │
│  ┌──────▼─────────────────▼──────────────────▼───────┐  │
│  │              Infrastructure Adapters               │  │
│  │   Datadog  │  GitHub  │  Slack  │  Kubernetes      │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Fluxo Principal (Scorecard + Remediação)

```
Deployment criado/atualizado
        │
        ▼
ScorecardController.on_resource_event()
        │
        ├─► ScorecardService.evaluate_resource()
        │       └─► 26+ regras validadas (6 pilares)
        │
        ├─► RemediationService.create_remediation_pr()  [se há issues remediáveis]
        │       ├─► Extrai DD_GIT_REPOSITORY_URL do Deployment
        │       ├─► Busca métricas de CPU/mem no Datadog
        │       ├─► Modifica deploy.yaml (nunca reduz valores)
        │       ├─► Cria branch + commit + Pull Request no GitHub
        │       └─► Notifica no Slack
        │
        ├─► RemediationWriter → AppRemediation CRD
        ├─► AppScorecardWriter → AppScorecard CRD
        └─► NamespaceNotificationBuffer → Slack digest
```

### Arquitetura Hexagonal (Ports & Adapters)

```
Domain Models  ←→  Application Services  ←→  Ports (interfaces)
                                                     ↕
                                           Infrastructure Adapters
                                       (GitHub, Datadog, Slack, K8s)
```

---

## 2. Stack Tecnológico Completo

| Categoria | Tecnologia | Versão |
|---|---|---|
| Linguagem | Python | 3.12 |
| K8s Operator Framework | Kopf | >=1.39.0 |
| Configuração | Pydantic + pydantic-settings | >=2.12.0 |
| HTTP Client (async) | httpx | >=0.27.0 |
| Slack SDK | slack-sdk | >=3.39.0 |
| Datadog Client | datadog-api-client | >=2.47.0 |
| Kubernetes Client | kubernetes | >=34.1.0 |
| YAML (preserva formatação) | ruamel.yaml | >=0.18.0 |
| Logging JSON | python-json-logger + structlog | >=4.0.0 / >=25.5.0 |
| Retry | backoff | >=2.2.1 |
| Testes | pytest + pytest-asyncio + pytest-mock | >=9.0.0 |
| Formatação | black | >=23.11.0 |
| Linting | flake8, mypy, pylint, ruff | múltiplos |
| Segurança | bandit, safety | múltiplos |
| Containerização | Docker (python:3.12-slim-bullseye) | - |
| Package Manager | Poetry | - |
| Deploy | Helm (charts/titlis-operator/) | - |

---

## 3. Todas as Variáveis de Ambiente

### Slack

```bash
SLACK_ENABLED=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_DEFAULT_CHANNEL=#titlis-notifications
SLACK_RATE_LIMIT_PER_MINUTE=60
SLACK_RATE_LIMIT_PER_HOUR=360
SLACK_TIMEOUT_SECONDS=10.0
SLACK_MAX_RETRIES=3
SLACK_ENABLED_SEVERITIES=info,warning,error,critical
SLACK_ENABLED_CHANNELS=operational,alerts
SLACK_MESSAGE_TITLE=Kopf Operator Notification
SLACK_INCLUDE_TIMESTAMP=true
SLACK_INCLUDE_CLUSTER_INFO=true
SLACK_INCLUDE_NAMESPACE=true
SLACK_MAX_MESSAGE_LENGTH=3000
```

### GitHub

```bash
GITHUB_ENABLED=true
GITHUB_TOKEN=ghp_...
GITHUB_BASE_BRANCH=develop
GITHUB_TIMEOUT_SECONDS=30.0
```

### Auto-Remediação — Defaults de Recursos

```bash
REMEDIATION_DEFAULT_CPU_REQUEST=100m
REMEDIATION_DEFAULT_CPU_LIMIT=500m
REMEDIATION_DEFAULT_MEMORY_REQUEST=128Mi
REMEDIATION_DEFAULT_MEMORY_LIMIT=512Mi
```

### Auto-Remediação — HPA

```bash
REMEDIATION_HPA_MIN_REPLICAS=2
REMEDIATION_HPA_MAX_REPLICAS=10
REMEDIATION_HPA_CPU_UTILIZATION=70
REMEDIATION_HPA_MEMORY_UTILIZATION=80
```

### Datadog

```bash
DD_API_KEY=...
DD_APP_KEY=...
DD_SITE=datadoghq.com
DD_ENV=production
DD_SERVICE=titlis-operator
# Obrigatório no env do Deployment para auto-remediação:
DD_GIT_REPOSITORY_URL=https://github.com/org/repo
```

### Kubernetes

```bash
KUBERNETES_NAMESPACE=titlis-system
SERVICE_ACCOUNT_NAME=titlis-operator
```

### Operador

```bash
RECONCILE_INTERVAL_SECONDS=300
DEBOUNCE_SECONDS=30
ENABLE_LEADER_ELECTION=true
LEADER_ELECTION_NAMESPACE=titlis
LOG_LEVEL=DEBUG
LOG_FORMAT=json
METRICS_ENABLED=true
TRACING_ENABLED=false
```

### Feature Flags

```bash
ENABLE_SCORECARD_CONTROLLER=true
ENABLE_SLO_CONTROLLER=true
ENABLE_AUTO_REMEDIATION=true
ENABLE_CASTAI_MONITOR=false
ENABLE_BACKSTAGE_ENRICHMENT=false
ENABLE_CASTAI_COST_ENRICHMENT=false
```

### Titlis API (comunicação operator → banco)

```bash
# Titlis API — comunicação operator → banco
TITLIS_API_ENABLED=false                                                  # feature flag
TITLIS_API_HOST=titlis-api.titlis-system.svc.cluster.local                # hostname do serviço
TITLIS_API_UDP_PORT=8125                                                   # porta TitlisUDP
TITLIS_API_HTTP_PORT=8080                                                  # porta REST

# Titlis API — banco de dados (configurado na API, não no operador)
DATABASE_URL=jdbc:postgresql://postgres:5432/titlis
DATABASE_USER=titlis_operator
DATABASE_PASSWORD=<secret>
DB_POOL_MAX=10
```

### Integrações Opcionais

```bash
BACKSTAGE_URL=https://backstage.company.com
BACKSTAGE_TOKEN=...
BACKSTAGE_CACHE_TTL_SECONDS=300

CASTAI_API_KEY=...
CASTAI_CLUSTER_ID=...
CASTAI_CLUSTER_NAME=develop
CASTAI_COST_CACHE_TTL_SECONDS=300
CASTAI_MONITOR_NAMESPACE=castai-agent
CASTAI_MONITOR_INTERVAL_SECONDS=60
```

---

## 4. Estrutura de Diretórios

```
jt-operator/
├── charts/titlis-operator/           # Helm chart
│   ├── crds/
│   │   ├── appremediations.titlis.io.yaml
│   │   ├── appscorecards.titlis.io.yaml
│   │   └── sloconfs.titlis.io.yaml
│   ├── templates/
│   │   ├── configmap.yaml
│   │   ├── deployment.yaml
│   │   ├── rbac.yaml
│   │   ├── service.yaml
│   │   └── serviceaccount.yaml
│   ├── Chart.yaml
│   └── values.yaml
├── config/
│   ├── env-validation-rules.yaml     # Regras de validação de env vars
│   └── scorecard-config.yaml         # Configuração de regras do scorecard
├── docs/
│   └── guia-extensao-scorecard.md
├── src/
│   ├── application/
│   │   ├── ports/                    # Interfaces (Hexagonal)
│   │   │   ├── datadog_port.py
│   │   │   ├── github_port.py
│   │   │   └── slack_port.py
│   │   └── services/                 # Lógica de negócio
│   │       ├── namespace_notification_buffer.py
│   │       ├── remediation_service.py
│   │       ├── scorecard_enricher.py
│   │       ├── scorecard_service.py
│   │       ├── slack_service.py
│   │       ├── slo_metrics_service.py
│   │       └── slo_service.py
│   ├── bootstrap/
│   │   └── dependencies.py           # Container de DI com @lru_cache
│   ├── controllers/                  # Handlers Kopf
│   │   ├── base.py
│   │   ├── castai_monitor_controller.py
│   │   ├── scorecard_controller.py
│   │   └── slo_controller.py
│   ├── domain/                       # Modelos de domínio puros
│   │   ├── enriched_scorecard.py
│   │   ├── github_models.py
│   │   ├── models.py
│   │   └── slack_models.py
│   ├── infrastructure/               # Adapters externos
│   │   ├── backstage/
│   │   │   └── enricher.py
│   │   ├── castai/
│   │   │   └── cost_enricher.py
│   │   ├── datadog/
│   │   │   ├── client.py
│   │   │   ├── factory.py
│   │   │   ├── repository.py
│   │   │   └── managers/
│   │   │       ├── base.py
│   │   │       ├── castai_metrics.py
│   │   │       ├── metrics.py
│   │   │       └── slo.py
│   │   ├── github/
│   │   │   ├── client.py
│   │   │   └── repository.py
│   │   ├── kubernetes/
│   │   │   ├── appscorecard_writer.py
│   │   │   ├── castai_health.py
│   │   │   ├── client.py
│   │   │   ├── k8s_status_writer.py
│   │   │   ├── remediation_writer.py
│   │   │   └── state_store.py
│   │   └── slack/
│   │       ├── message_builder.py
│   │       └── repository.py
│   ├── utils/
│   │   ├── json_logger.py
│   │   └── logging_bootstrap.py
│   ├── __init__.py
│   ├── main.py                       # Entry point Kopf
│   └── settings.py                   # Pydantic settings
├── tests/
│   ├── integration/
│   │   ├── test_mocked_datadog.py
│   │   └── test_mocked_slack.py
│   ├── unit/
│   │   ├── test_castai_monitor.py
│   │   ├── test_controllers.py
│   │   ├── test_datadog.py
│   │   ├── test_domain_models.py
│   │   ├── test_github_repository.py
│   │   ├── test_logging.py
│   │   ├── test_remediation_service.py
│   │   ├── test_services.py
│   │   ├── test_settings.py
│   │   └── test_slack.py
│   ├── conftest.py
│   ├── mock_kopf.py
│   └── __init__.py
├── Dockerfile
├── Makefile
├── README.md
├── pyproject.toml
├── pytest.ini
└── poetry.lock
```

---

## 5. Serviços, Jobs e Models

### Domain Models (`src/domain/`)

#### `models.py`

| Classe | Descrição |
|---|---|
| `ComplianceStatus` | Enum: COMPLIANT, NON_COMPLIANT, UNKNOWN, PENDING |
| `ServiceTier` | Enum: TIER_1..TIER_4 |
| `SLOTimeframe` | Enum: 7d, 30d, 90d |
| `SLOType` | Enum: METRIC, MONITOR, TIME_SLICE |
| `SLOAppFramework` | Enum: WSGI, FASTAPI, AIOHTTP |
| `ValidationPillar` | Enum: RESILIENCE, SECURITY, COST, PERFORMANCE, OPERATIONAL, COMPLIANCE |
| `ValidationRuleType` | Enum: BOOLEAN, NUMERIC, ENUM, REGEX |
| `ValidationSeverity` | Enum: CRITICAL, ERROR, WARNING, INFO, OPTIONAL |
| `ValidationRule` | Config de uma regra (id, pillar, severity, weight, remediation) |
| `ValidationResult` | Resultado de uma validação (passed, message, actual_value) |
| `PillarScore` | Score de um pilar (0-100, passed_checks, weighted_score) |
| `ResourceScorecard` | Scorecard completo de um workload |
| `ScorecardConfig` | Configuração do sistema de scorecard (regras, thresholds) |
| `SLO` | Definição de SLO |
| `ServiceDefinition` | Definição de serviço no Datadog |
| `SLOConfigSpec` | Spec do CRD SLOConfig — inclui `auto_detect_framework: bool` (detecta framework via Datadog tag ou annotation K8s) |
| `SLOConfigStatus` | Status do CRD SLOConfig — inclui `detected_framework: str` (framework detectado automaticamente) |

#### `github_models.py`

| Classe | Descrição |
|---|---|
| `DatadogProfilingMetrics` | CPU/mem médios do Datadog; métodos `suggest_*()` |
| `RemediationIssue` | Uma issue remediável (rule_id, category: resources/hpa) |
| `RemediationFile` | Arquivo a commitar no PR (path, content) |
| `PullRequestResult` | PR criado (number, title, url, branch) |
| `RemediationRequest` | Request de remediação (resource, issues, body) |
| `RemediationResult` | Resultado da tentativa (success, pull_request, error) |

#### `slack_models.py`

| Classe | Descrição |
|---|---|
| `NotificationSeverity` | Enum: INFO, WARNING, ERROR, CRITICAL |
| `SlackNotification` | Mensagem Slack (title, message, severity, channel) |

### Application Services (`src/application/services/`)

| Serviço | Responsabilidade |
|---|---|
| `ScorecardService` | Avalia 26+ regras nos workloads, calcula pillar scores |
| `ScorecardEnricher` | Enriquece scorecard com dados de Backstage e CAST AI |
| `RemediationService` | Orquestra auto-remediação: Datadog → YAML → GitHub PR → Slack |
| `SLOService` | Reconcilia SLOConfig CRDs com Datadog (create/update/noop); auto-detecta framework via annotation K8s → Datadog tag → fallback WSGI; idempotência em 3 caminhos (fast-path por `known_slo_id`, orphan check por tag, fluxo original) |
| `SLOMetricsService` | Coleta métricas de SLO do Datadog |
| `SlackService` | Dispara notificações Slack com rate limiting |
| `NamespaceNotificationBuffer` | Agrega scorecards por namespace e envia digest em lote |

### Controllers Kopf (`src/controllers/`)

| Controller | Triggers K8s | Responsabilidade |
|---|---|---|
| `ScorecardController` | resume/create/update/delete em `apps/v1/deployments` | Avalia scorecard, triggera remediação, escreve CRDs |
| `SLOController` | create/update/delete em SLOConfig CRD | Sincroniza SLOs com Datadog; extrai `known_slo_id` de `status.slo_id` e `k8s_annotations` de `metadata.annotations` para passar ao `SLOService`; persiste `detected_framework` no status |
| `CastaiMonitorController` | Loop periódico | Monitora saúde do agente CAST AI |
| `BaseController` | — | Helpers: status update, Slack seguro, namespace exclusion |

### Ports (Interfaces) (`src/application/ports/`)

| Port | Métodos principais |
|---|---|
| `GitHubPort` | `branch_exists`, `create_branch`, `get_file_content`, `commit_files`, `create_pull_request`, `find_open_remediation_pr` |
| `DatadogPort` | `get_service_definition`, `get_service_slos`, `create_slo`, `update_slo_apps`, `get_container_metrics`, `find_slo_by_tags` |
| `SlackPort` | `send_notification`, `test_connection`, `is_enabled` |

### Infrastructure Adapters (`src/infrastructure/`)

| Adapter | Porta implementada |
|---|---|
| `GitHubRepository` | `GitHubPort` — HTTP async com httpx |
| `DatadogRepository` | `DatadogPort` — datadog-api-client oficial |
| `SlackRepository` | `SlackPort` — slack-sdk AsyncWebClient/AsyncWebhookClient |
| `AppScorecardWriter` | — Escreve AppScorecard CRD via kubernetes client |
| `RemediationWriter` | — Escreve AppRemediation CRD via kubernetes client |
| `KubernetesStatusWriter` | — Atualiza `.status` de CRDs |
| `BackstageEnricher` | — HTTP GET para Backstage catalog API |
| `CastaiCostEnricher` | — HTTP GET para CAST AI cost API |

### CRDs Customizados

| CRD | Grupo/Versão | Propósito |
|---|---|---|
| `AppScorecard` | `titlis.io/v1` | Resultado de avaliação de maturidade |
| `AppRemediation` | `titlis.io/v1` | Registro de PR de remediação criado |
| `SLOConfig` | `titlis.io/v1` | Definição declarativa de SLO |

### Regras de Validação (26+)

| ID | Pilar | Remediável | Descrição |
|---|---|---|---|
| RES-001 | RESILIENCE | Não | Liveness Probe configurada |
| RES-002 | RESILIENCE | Não | Readiness Probe configurada |
| RES-003 | RESILIENCE | **Sim** | CPU Requests definido |
| RES-004 | RESILIENCE | **Sim** | CPU Limits definido |
| RES-005 | RESILIENCE | **Sim** | Memory Requests definido |
| RES-006 | RESILIENCE | **Sim** | Memory Limits definido |
| RES-007 | RESILIENCE | **Sim** | HPA configurado (alta carga) |
| RES-008 | RESILIENCE | **Sim** | HPA utilization targets adequados |
| PERF-001 | PERFORMANCE | **Sim** | Resource requests definidos |
| PERF-002 | PERFORMANCE | **Sim** | HPA targets otimizados |

**Regras remediáveis por categoria:**
- **resources**: RES-003, RES-004, RES-005, RES-006, PERF-001
- **hpa**: RES-007, RES-008, PERF-002

---

## 6. Doze Common Hurdles (com Soluções)

### H-01: Remediação não inicia — falta DD_GIT_REPOSITORY_URL

**Sintoma:** `create_remediation_pr` retorna `RemediationResult(success=False, error="DD_GIT_REPOSITORY_URL not found")`

**Causa:** O Deployment não tem a variável de ambiente `DD_GIT_REPOSITORY_URL` no container spec.

**Solução:** Adicionar ao Deployment:
```yaml
env:
  - name: DD_GIT_REPOSITORY_URL
    value: https://github.com/org/repo
```
A URL deve apontar para o repositório GitHub que contém `manifests/kubernetes/main/deploy.yaml`.

---

### H-02: PR duplicado criado para o mesmo workload

**Sintoma:** Múltiplos PRs abertos com `fix/auto-remediation-{namespace}-{resource}-`.

**Causa:** `_pending` set foi limpo (restart do operador) e `find_open_remediation_pr` não encontrou o PR existente porque o título mudou.

**Solução:** O operador verifica PRs abertos via `find_open_remediation_pr` que busca pelo padrão do branch. Ao reiniciar, verificar se há PRs abertos antes de re-triggerar. O CRD `AppRemediation` pode ser consultado para saber se já existe PR em andamento.

---

### H-03: Valores de CPU/memória sendo reduzidos incorretamente

**Sintoma:** O PR sugere redução de `requests.cpu` de `500m` para `100m`.

**Causa:** `_keep_max` não está sendo chamado ou parser não reconhece o formato.

**Solução:** Verificar em `remediation_service.py`:
- `_parse_cpu_millicores` suporta: `"500m"`, `"0.5"`, `"1"` (sem sufixo = cores inteiros)
- `_parse_memory_mib` suporta: `"512Mi"`, `"0.5Gi"`, `"512M"`, `"512"` (bytes)
- `_keep_max(current, suggested, parser)` sempre retorna o maior valor

---

### H-04: Slack não recebe notificações

**Sintoma:** Operator roda mas sem mensagens no Slack.

**Causa:** Múltiplas possíveis: `SLACK_ENABLED=false`, severity filtrada, rate limit atingido, token inválido.

**Debug:** Verificar em ordem:
1. `SLACK_ENABLED=true` no env
2. `SLACK_ENABLED_SEVERITIES` inclui a severity desejada
3. Rate limit: `SLACK_RATE_LIMIT_PER_MINUTE` (default 60)
4. Testar conexão: `SlackRepository.test_connection()`
5. Logs: buscar `"slack"` no output JSON

---

### H-05: Scorecard avalia namespace excluído

**Sintoma:** `kube-system` ou outros namespaces de sistema sendo avaliados.

**Causa:** `excluded_namespaces` em `ScorecardConfig` não está configurado.

**Solução:** Em `config/scorecard-config.yaml`:
```yaml
excluded_namespaces:
  - kube-system
  - kube-public
  - kube-node-lease
  - cert-manager
  - monitoring
```

---

### H-06: SLO criado no Datadog com dados errados

**Sintoma:** SLO criado com target diferente do especificado no CRD.

**Causa:** Validação do `SLOController` permite `warning > target` invertido, ou `target` fora de 0-100.

**Solução:** Conferir validação em `slo_controller.py`:
- `warning` deve ser **menor** que `target` (ex: target=99.9, warning=99.0)
- Ambos entre 0 e 100
- Para tipo METRIC: obrigatório `app_framework`, (`numerator` + `denominator`), **ou** `auto_detect_framework: true`

---

### H-13: Framework detectado incorretamente (fallback WSGI inesperado)

**Sintoma:** SLO criado com queries WSGI, mas o serviço usa FastAPI/aiohttp. `status.detected_framework` mostra `"wsgi"` com `detection_source: fallback`.

**Causa:** Nenhuma das fontes de detecção encontrou o framework: (1) sem annotation `titlis.io/app-framework` no recurso SLOConfig, e (2) a Datadog Service Definition do serviço não tem tag `framework:<value>`.

**Solução:** Escolha uma das três abordagens:
```yaml
# Opção 1 — annotation no SLOConfig (maior precedência)
metadata:
  annotations:
    titlis.io/app-framework: fastapi

# Opção 2 — tag na Datadog Service Definition do serviço
tags:
  - framework:fastapi

# Opção 3 — campo explícito no spec (ignora auto-detecção)
spec:
  app_framework: fastapi
  auto_detect_framework: false
```
Verificar no log: campo `detection_source` indica `"annotation"`, `"datadog_tag"` ou `"fallback"`.

---

### H-07: HPA criado com minReplicas maior que maxReplicas

**Sintoma:** K8s rejeita o HPA criado pelo PR com erro de validação.

**Causa:** `_build_hpa_manifest_dict` usa `max(current, settings.hpa_min_replicas)` mas `max_replicas` não foi ajustado proporcionalmente.

**Solução:** Verificar que `REMEDIATION_HPA_MAX_REPLICAS > REMEDIATION_HPA_MIN_REPLICAS` no env. O operador usa os defaults do settings se não há HPA atual.

---

### H-08: Datadog não retorna métricas — defaults usados sempre

**Sintoma:** PR sempre usa valores default (100m CPU, 128Mi mem) mesmo para apps com alto consumo.

**Causa:** `get_container_metrics` retorna `None` porque `DD_API_KEY`/`DD_APP_KEY` inválidas ou deployment_name não bate com o nome no Datadog.

**Solução:**
1. Verificar credenciais Datadog nas secrets
2. Confirmar que o Deployment tem tag `service` no Datadog matching o nome K8s
3. Testar diretamente: `DatadogRepository.get_container_metrics(name, namespace)`

---

### H-09: `@lru_cache` retorna instância antiga após mudança de settings

**Sintoma:** Mudança em ENV var não reflete na instância do serviço.

**Causa:** `@lru_cache` em `dependencies.py` cria singleton na primeira chamada e não invalida.

**Solução:** Reiniciar o pod do operador. Em testes, usar `functools.lru_cache.cache_clear()` nos fixtures ou mockar os getters diretamente.

---

### H-10: Kopf para de processar eventos sem logs de erro

**Sintoma:** Deployments são criados/atualizados mas o controller não reage.

**Causa:** Handler lançou exceção não tratada causando crash silencioso do worker, ou leader election perdida.

**Solução:**
1. Verificar logs do pod: `kubectl logs -n titlis-system <pod>`
2. Checar se `ENABLE_LEADER_ELECTION=true` e se outro pod assumiu a liderança
3. Confirmar que o ServiceAccount tem RBAC adequado (`charts/titlis-operator/templates/rbac.yaml`)

---

### H-11: ruamel.yaml não preserva comentários no deploy.yaml

**Sintoma:** PR remove comentários existentes do arquivo YAML.

**Causa:** `_modify_deploy_yaml` usa `ruamel.yaml` com `RoundTripLoader` que deve preservar comentários, mas a serialização pode perdê-los em edge cases.

**Solução:** Confirmar que o load usa `yaml = YAML()` (não `YAML(typ='safe')`). Evitar misturar `ruamel.yaml` com `PyYAML` no mesmo arquivo.

---

### H-12: Testes falham com `ModuleNotFoundError: kopf`

**Sintoma:** `pytest` falha na importação de controllers que importam kopf.

**Causa:** Kopf usa decoradores que registram handlers globalmente; em testes, o módulo precisa ser mockado.

**Solução:** O projeto tem `tests/mock_kopf.py` com mocks dos decoradores Kopf. Usar o `conftest.py` que injeta esses mocks antes de importar os controllers. Nunca importar controllers diretamente sem o mock ativo.

---

## 7. Quinze Design Patterns do Projeto

### P-01: Hexagonal Architecture (Ports & Adapters)
Domínio isolado de infraestrutura via interfaces abstratas em `src/application/ports/`. Serviços dependem de ports, nunca de adapters diretamente. Facilita testes (mock ports) e troca de providers.

### P-02: Dependency Injection via lru_cache
`src/bootstrap/dependencies.py` usa `@lru_cache()` para criar singletons lazy. Cada `get_*()` function encapsula inicialização e retorna a mesma instância. Feature flags controlam o que é inicializado.

### P-03: Feature Flags por ENV
Cada funcionalidade major (`ENABLE_SCORECARD_CONTROLLER`, `ENABLE_AUTO_REMEDIATION`, etc.) pode ser desligada sem rebuild. Controllers registrados condicionalmente em `src/main.py`.

### P-04: Never-Reduce (Immutable Floor)
`_keep_max(current, suggested, parser)` garante que auto-remediação nunca reduza CPU/memória de containers. Princípio: o operador só melhora, nunca degrada o que já existe.

### P-05: HPA Utilization Minimization
Para targets de HPA (CPU%, mem%), usa `min(current, default)` em vez de `max`. Lógica inversa deliberada: target menor = escala mais cedo = mais agressivo = melhor para resiliência.

### P-06: Idempotency via Memory + API Check
`RemediationService` mantém `_pending: Set[str]` em memória para bloquear runs concorrentes no mesmo recurso. Antes de criar PR, verifica via GitHub API se já há PR aberto. Dupla proteção.

### P-07: Batch Notifications (Namespace Digest)
`NamespaceNotificationBuffer` coleta scorecards de todos os workloads de um namespace e envia um único digest Slack em vez de uma mensagem por Deployment. Reduz ruído em clusters com muitos apps.

### P-08: Graceful Degradation
Serviços opcionais (Slack, Backstage, CAST AI) inicializam como `None` se desabilitados. Controllers verificam `if service is None` antes de usar. Operador funciona mesmo sem integrações externas.

### P-09: Kopf Handler Delegation
Handlers Kopf são thin — apenas capturam kwargs e delegam para métodos de Controller classes. Ex: `@kopf.on.create` chama `ScorecardController().on_resource_event(body, **kwargs)`. Desacopla framework do negócio.

### P-10: Structured JSON Logging
Todos os logs são JSON via `python-json-logger` + `structlog`. Campos padronizados: `event`, `namespace`, `resource`, `pillar`, `score`, `rule_id`. Facilita ingestão em Datadog/Elasticsearch.

### P-11: Pydantic Settings com Nested Models
`Settings` em `src/settings.py` usa composição de sub-models Pydantic (`SlackSettings`, `GitHubSettings`, `RemediationSettings`). Validação de tipos em startup. Defaults sensatos via `Field(default=...)`.

### P-12: Repository Pattern
Cada adapter externo implementa um Repository que traduz chamadas de domínio para API específica. `GitHubRepository`, `DatadogRepository`, `SlackRepository` são a única camada que conhece detalhes de HTTP/SDK.

### P-13: YAML Round-Trip Preservation
Usa `ruamel.yaml` (não PyYAML) para ler, modificar e reescrever `deploy.yaml` sem destruir formatação, comentários e ordem de chaves. Crítico para PRs aceitáveis pelos desenvolvedores.

### P-14: CRD as State Store
`AppScorecard` e `AppRemediation` CRDs servem como estado persistente do operador. Permitem que outros sistemas consultem o estado atual sem acesso interno ao operador. Status subresource atualizado após cada evento.

### P-15: Three-Path SLO Idempotency
`SLOService.reconcile_slo` usa três caminhos de execução para garantir idempotência completa:
- **Path A** (`known_slo_id` presente) — fast path no restart: usa `status.slo_id` diretamente, salta toda busca, chama `update_slo_apps` direto.
- **Path B** (`known_slo_id` ausente, `resource_uid` presente) — orphan safety check: busca via `find_slo_by_tags(["titlis_resource_uid:<uid>"])` antes de criar, evita SLOs duplicados após crash/restore.
- **Path C** — fluxo original: `get_service_slos` → `check_and_update_existing_slo` → create. Todo SLO criado recebe tag `titlis_resource_uid:<k8s-uid>` para permitir Path B em reconciliações futuras.

---

## 8. Pipeline Semanal Completo

> Sugestão de pipeline de manutenção e qualidade para equipes operando o Titlis Operator.

### Segunda-feira — 09:00: Revisão de Scorecard

```
09:00 - Revisar AppScorecards com overall_score < 70
09:30 - Verificar AppRemediations com phase=Failed
10:00 - Triage: quais PRs de remediação precisam de merge manual
```

### Terça-feira — 10:00: SLO Health Check

```
10:00 - Verificar SLOConfigs com state=error no status
10:15 - Checar SLOConfigs com detected_framework=wsgi e detection_source=fallback (possível framework errado)
10:30 - Checar compliance dos SLOs no Datadog (target vs actual)
11:00 - Atualizar thresholds se necessário
```

### Quarta-feira — 14:00: Security Scan

```
14:00 - make lint (black, flake8, mypy, pylint)
14:30 - poetry run bandit -r src/
15:00 - poetry run safety check
15:30 - Revisar alertas de dependências desatualizadas
```

### Quinta-feira — 09:00: Testes e Cobertura

```
09:00 - make test-coverage (meta: >= 70%)
09:30 - Revisar falhas de teste
10:00 - Adicionar testes para novas features da semana
11:00 - make test-unit para validação final
```

### Sexta-feira — 15:00: Release e Documentação

```
15:00 - Revisar PRs pendentes de remediação auto-gerados
15:30 - Tag de versão se houver mudanças significativas
16:00 - Atualizar CLAUDE.md se novos padrões foram identificados
16:30 - Retrospectiva rápida de issues da semana
```

### Contínuo (a cada push/PR):

```
CI: make lint && make test-unit
CD: build Docker image + push para registry
Deploy: helm upgrade --install titlis-operator charts/titlis-operator/
```

---

## 9. Checklist Pós-Implementação

Execute **todos os itens** após qualquer alteração no código:

### Qualidade de Código

- [ ] `make lint` passa sem erros (`black`, `flake8`, `mypy`, `pylint`)
- [ ] `make format` rodado se black reportou diffs
- [ ] Sem `type: ignore` sem justificativa em comentário
- [ ] Sem `# noqa` sem justificativa em comentário

### Testes

- [ ] `make test-unit` — todos os testes passam
- [ ] `make test-coverage` — cobertura >= 70%
- [ ] Novos testes escritos para todo código novo
- [ ] Mocks usam `AsyncMock` para métodos async
- [ ] Fixtures em `conftest.py` quando reutilizáveis

### Segurança

- [ ] Nenhuma credencial hardcoded (usar ENV vars via `settings.py`)
- [ ] Inputs externos validados pelo Pydantic
- [ ] `DD_GIT_REPOSITORY_URL` validado antes de usar
- [ ] RBAC mínimo necessário em `charts/titlis-operator/templates/rbac.yaml`

### Arquitetura

- [ ] Novos serviços externos adicionados como Port + Adapter (hexagonal)
- [ ] Novas dependências inicializadas em `src/bootstrap/dependencies.py`
- [ ] Feature flag adicionada para novas funcionalidades major
- [ ] Logs JSON estruturados com campos: `event`, `namespace`, `resource`

### Remediação (se alterou `remediation_service.py`)

- [ ] `_keep_max` chamado em todos os valores de CPU/memória
- [ ] HPA usa `min()` para utilization, `max()` para replicas
- [ ] PR search funciona com `find_open_remediation_pr`
- [ ] `_pending` set limpo após conclusão (sucesso ou erro)
- [ ] Testes em `test_remediation_service.py` atualizados

### Configuração

- [ ] Novas ENV vars documentadas neste CLAUDE.md (seção 3)
- [ ] Valores default razoáveis em `settings.py` via `Field(default=...)`
- [ ] `charts/titlis-operator/templates/configmap.yaml` ou `values.yaml` atualizados se necessário

### CRDs (se alterou schemas)

- [ ] `charts/titlis-operator/crds/*.yaml` atualizados
- [ ] Versão de API incrementada se houver breaking changes
- [ ] `to_dict()` atualizado se campos foram adicionados ao modelo

### Deploy

- [ ] Dockerfile não precisa de rebuild se apenas mudanças de config
- [ ] `pyproject.toml` atualizado se novas dependências adicionadas
- [ ] `poetry.lock` committed junto com `pyproject.toml`

---

## 10. Comandos de Desenvolvimento Rápido

```bash
# Setup inicial
poetry install

# Desenvolvimento
make test-unit          # Testes unitários
make test-coverage      # Testes + coverage report
make lint               # Todos os linters
make format             # Auto-format com black

# Testes específicos
make test-settings      # test_settings.py
make test-datadog       # test_datadog.py
make test-slack         # test_slack.py
make test-services      # test_services.py
make test-controllers   # test_controllers.py

# Pattern matching
PATTERN=remediation make test-pattern

# Rodar localmente (requer kubeconfig)
make run

# Full cycle
make dev               # clean + dev-install + test + lint
```

---

## 11. Regras Obrigatórias para Claude

1. **Sempre** rodar `make lint && make test-unit` após qualquer mudança
2. **Nunca** reduzir valores de CPU/memória — usar `_keep_max()`
3. **Nunca** criar adaptadores que não implementem a Port correspondente
4. **Nunca** hardcodar credenciais — sempre via `settings.py` / ENV
5. **Sempre** usar `AsyncMock` para métodos async em testes
6. **Sempre** adicionar logging JSON estruturado em código novo
7. **Sempre** verificar feature flag antes de inicializar dependências opcionais
8. **Nunca** importar infrastructure diretamente de domain ou controllers — sempre via ports
9. Novos serviços externos = Port interface + Adapter + DI em `dependencies.py`
10. Novas ENV vars = documentar na seção 3 deste arquivo e em `settings.py` com `Field()`
11. **Nunca** adicionar docstrings — nem de módulo, classe ou função. Lint padrão é **flake8** (regras D100–D107 ignoradas)

---

## 12. Documentação de Referência

| Documento | Propósito |
|-----------|-----------|
| [docs/modelagem-dados.md](docs/modelagem-dados.md) | DDL completo (estado atual) + schema evolution fases 1–7 |
| [docs/rules-and-evolution.md](docs/rules-and-evolution.md) | Regras de código consolidadas + roadmap por fase |
| [docs/evolution-checklist.md](docs/evolution-checklist.md) | Checklist de progresso do roadmap |
| [docs/guia-extensao-scorecard.md](docs/guia-extensao-scorecard.md) | Guia para adicionar novas regras de validação |
| [docs/scorecard-rules.md](docs/scorecard-rules.md) | Todas as 23 regras de validação com detalhamento completo |
| [docs/guia-titlis-api-kotlin.md](docs/guia-titlis-api-kotlin.md) | Passo a passo para criar a Titlis API (Kotlin/Ktor) e integrar ao operador |

---

## 13. Banco de Dados Relacional

### Status atual

> **Schema criado, não integrado.** O operador persiste estado exclusivamente via CRDs Kubernetes.
> O banco de dados é uma camada de observabilidade futura — não é pré-requisito para operação.

### Script de criação

```bash
# Criar o banco (uma única vez em cada ambiente):
psql -U postgres -c "CREATE DATABASE titlis;"
psql -U postgres -d titlis -f db/schema.sql
```

### Estrutura de schemas

| Schema | SLA | Propósito |
|--------|-----|-----------|
| `titlis_oltp` | < 5ms | Estado atual de workloads, scorecards, remediações e SLOs |
| `titlis_audit` | 100% completude | Histórico SCD Type 4 + audit trail de notificações |
| `titlis_ts` | volume | Métricas CPU/mem e scores time-series (candidato a TimescaleDB) |

### Tabelas principais

| Tabela | Schema | Descrição |
|--------|--------|-----------|
| `clusters` | `titlis_oltp` | Clusters Kubernetes registrados |
| `namespaces` | `titlis_oltp` | Namespaces por cluster |
| `workloads` | `titlis_oltp` | Deployments rastreados (soft-delete) |
| `validation_rules` | `titlis_oltp` | Catálogo imutável das 26+ regras |
| `app_scorecards` | `titlis_oltp` | Estado atual do scorecard (1 por workload — SCD Type 4) |
| `pillar_scores` | `titlis_oltp` | Score por pilar do scorecard atual |
| `validation_results` | `titlis_oltp` | Resultado por regra do scorecard atual |
| `app_remediations` | `titlis_oltp` | Estado atual da remediação (1 por workload — SCD Type 4) |
| `remediation_issues` | `titlis_oltp` | Issues individuais vinculadas à remediação |
| `slo_configs` | `titlis_oltp` | Estado atual dos SLOConfig CRDs |
| `app_scorecard_history` | `titlis_audit` | Histórico de scorecards com snapshot JSONB |
| `pillar_score_history` | `titlis_audit` | Histórico granular por pilar |
| `remediation_history` | `titlis_audit` | Log de todas as transições de estado de remediação |
| `slo_compliance_history` | `titlis_audit` | Histórico de sincronizações com o Datadog |
| `notification_log` | `titlis_audit` | Auditoria de notificações Slack |
| `resource_metrics` | `titlis_ts` | CPU/memória coletados do Datadog |
| `scorecard_scores` | `titlis_ts` | Série temporal plana para Grafana/Metabase |

### Padrões de design do banco

- **PKs como BIGINT IDENTITY**: PKs no formato `<nome_da_tabela>_id BIGINT GENERATED ALWAYS AS IDENTITY` — evita fragmentação de UUID randômico e mantém ordem de inserção.
- **Nomes compostos**: colunas `name`, `type`, `status` standalone são proibidas; usar sempre prefixo composto (`cluster_name`, `app_remediation_status`, etc.).
- **VARCHAR sobre TEXT**: preferir `VARCHAR(n)` quando o tamanho máximo é conhecido; `TEXT` apenas quando indefinido (ex: mensagens de erro).
- **SCD Type 4**: tabela corrente (`app_scorecards`) + tabela histórica separada (`app_scorecard_history`). A **aplicação** arquiva o estado anterior quando `version` muda (sem triggers DML).
- **Never-FK em audit**: tabelas `titlis_audit.*` usam referências lógicas (sem FK constraint) — histórico sobrevive à deleção de workloads.
- **Snapshot JSONB**: `pillar_scores` e `validation_results` desnormalizados como JSONB no histórico eliminam joins analíticos.
- **Sem triggers DML**: `updated_at` e audit trail gerenciados pela aplicação — sem functions/triggers que modifiquem dados no banco.
- **Particionamento futuro**: `titlis_audit.*` e `titlis_ts.*` projetados para `PARTITION BY RANGE` trimestral via `pg_partman`.

### Quando integrar

A integração com o banco deve ocorrer quando houver necessidade de:
- Dashboard/frontend com histórico de scores
- Queries analíticas (top regras que mais falham, taxa de sucesso de remediações)
- Auditoria de notificações Slack com retenção longa
- Métricas time-series independentes do Datadog

