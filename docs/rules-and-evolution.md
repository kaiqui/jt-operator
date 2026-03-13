# Titlis Operator — Regras Atuais e Planejamento de Evolução

> Referência consolidada: todas as regras obrigatórias de código, arquitetura e qualidade,
> seguidas do planejamento completo de evolução por fase.

---

## Parte I — Regras Obrigatórias (Estado Atual)

### 1. Regras de Código

| # | Regra | Contexto de Aplicação |
|---|-------|-----------------------|
| R-01 | Sempre rodar `make lint && make test-unit` após qualquer mudança | Todo PR / toda alteração |
| R-02 | Nunca reduzir valores de CPU/memória — usar `_keep_max()` | `remediation_service.py` |
| R-03 | HPA usa `min()` para utilization targets, `max()` para replicas | `remediation_service.py` |
| R-04 | Nunca criar adaptadores que não implementem a Port correspondente | Toda infraestrutura externa |
| R-05 | Nunca hardcodar credenciais — sempre via `settings.py` / ENV | Todo o código |
| R-06 | Sempre usar `AsyncMock` para métodos async em testes | `tests/unit/`, `tests/integration/` |
| R-07 | Sempre adicionar logging JSON estruturado em código novo | Todo código novo |
| R-08 | Sempre verificar feature flag antes de inicializar dependências opcionais | `src/bootstrap/dependencies.py` |
| R-09 | Nunca importar infrastructure diretamente de domain ou controllers — sempre via ports | Domain, Controllers |
| R-10 | Novos serviços externos = Port interface + Adapter + DI em `dependencies.py` | Infraestrutura nova |
| R-11 | Novas ENV vars = documentar na seção 3 do CLAUDE.md e em `settings.py` com `Field()` | Configuração |
| R-12 | **Nunca adicionar docstrings** — nem de módulo, classe ou função | Todo o código |
| R-13 | Lint padrão é **flake8** — único gate de estilo obrigatório | CI/CD, pre-commit |

### 2. Regras de Remediação Automática

| # | Regra |
|---|-------|
| RR-01 | `_keep_max(current, suggested, parser)` chamado em todos os valores de CPU/memória |
| RR-02 | `_parse_cpu_millicores` suporta: `"500m"`, `"0.5"`, `"1"` (sem sufixo = cores inteiros) |
| RR-03 | `_parse_memory_mib` suporta: `"512Mi"`, `"0.5Gi"`, `"512M"`, `"512"` (bytes) |
| RR-04 | PR search via `find_open_remediation_pr` antes de criar novo PR |
| RR-05 | `_pending` set limpo após conclusão (sucesso ou erro) |
| RR-06 | Deployment deve ter `DD_GIT_REPOSITORY_URL` para trigger de remediação |
| RR-07 | Testes em `test_remediation_service.py` atualizados após qualquer mudança |

### 3. Regras de Arquitetura

| # | Regra |
|---|-------|
| RA-01 | Arquitetura Hexagonal (Ports & Adapters) — domínio isolado de infraestrutura |
| RA-02 | Dependency Injection via `@lru_cache` em `src/bootstrap/dependencies.py` |
| RA-03 | Feature flags por ENV — cada funcionalidade major pode ser desligada sem rebuild |
| RA-04 | Novos CRDs = versão de API incrementada se houver breaking changes |
| RA-05 | `AppScorecard` e `AppRemediation` CRDs como state store persistente |
| RA-06 | Handlers Kopf são thin — apenas capturam kwargs e delegam para métodos de Controller |

### 4. Regras de Testes

| # | Regra |
|---|-------|
| RT-01 | `make test-coverage` — cobertura mínima de 70% |
| RT-02 | Novos testes escritos para todo código novo |
| RT-03 | Fixtures em `conftest.py` quando reutilizáveis |
| RT-04 | Nunca importar controllers diretamente sem o mock Kopf ativo (`tests/mock_kopf.py`) |
| RT-05 | Mocks de banco devem usar dados reais — não mockar banco em testes de integração |

### 5. Regras de Segurança

| # | Regra |
|---|-------|
| RS-01 | Nenhuma credencial hardcoded — usar ENV vars via `settings.py` |
| RS-02 | Inputs externos validados pelo Pydantic |
| RS-03 | `DD_GIT_REPOSITORY_URL` validado antes de usar |
| RS-04 | RBAC mínimo necessário em `charts/titlis-operator/templates/rbac.yaml` |

### 6. Regras de Logging

Campos obrigatórios nos logs JSON:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `event` | string | Nome do evento (ex: `"scorecard_evaluated"`) |
| `namespace` | string | Namespace Kubernetes |
| `resource` | string | Nome do workload |
| `pillar` | string | Pilar do scorecard (quando aplicável) |
| `score` | number | Score calculado (quando aplicável) |
| `rule_id` | string | ID da regra (quando aplicável) |

### 7. Checklist Pós-Implementação Completo

#### Qualidade de Código
- [ ] `make lint` passa sem erros (`black`, `flake8`, `mypy`, `pylint`)
- [ ] `make format` rodado se black reportou diffs
- [ ] Sem `type: ignore` sem justificativa em comentário
- [ ] Sem `# noqa` sem justificativa em comentário

#### Testes
- [ ] `make test-unit` — todos os testes passam
- [ ] `make test-coverage` — cobertura >= 70%
- [ ] Novos testes escritos para todo código novo
- [ ] Mocks usam `AsyncMock` para métodos async
- [ ] Fixtures em `conftest.py` quando reutilizáveis

#### Segurança
- [ ] Nenhuma credencial hardcoded
- [ ] Inputs externos validados pelo Pydantic
- [ ] `DD_GIT_REPOSITORY_URL` validado antes de usar
- [ ] RBAC mínimo em `charts/titlis-operator/templates/rbac.yaml`

#### Arquitetura
- [ ] Novos serviços externos adicionados como Port + Adapter (hexagonal)
- [ ] Novas dependências inicializadas em `src/bootstrap/dependencies.py`
- [ ] Feature flag adicionada para novas funcionalidades major
- [ ] Logs JSON estruturados com campos: `event`, `namespace`, `resource`

#### Remediação (se alterou `remediation_service.py`)
- [ ] `_keep_max` chamado em todos os valores de CPU/memória
- [ ] HPA usa `min()` para utilization, `max()` para replicas
- [ ] PR search funciona com `find_open_remediation_pr`
- [ ] `_pending` set limpo após conclusão
- [ ] Testes em `test_remediation_service.py` atualizados

#### Configuração
- [ ] Novas ENV vars documentadas no CLAUDE.md (seção 3)
- [ ] Valores default razoáveis em `settings.py` via `Field(default=...)`
- [ ] `charts/titlis-operator/templates/configmap.yaml` ou `values.yaml` atualizados

#### CRDs (se alterou schemas)
- [ ] `charts/titlis-operator/crds/*.yaml` atualizados
- [ ] Versão de API incrementada se houver breaking changes
- [ ] `to_dict()` atualizado se campos foram adicionados ao modelo

#### Deploy
- [ ] `pyproject.toml` atualizado se novas dependências adicionadas
- [ ] `poetry.lock` committed junto com `pyproject.toml`

---

## Parte II — Planejamento de Evolução

### Visão Geral das Fases

```
2026 Q1    2026 Q2    2026 Q3    2026 Q4    2027 Q1    2027 Q2    2027 Q3
───────────────────────────────────────────────────────────────────────────
 Fase 1     Fase 2     Fase 3     Fase 4     Fase 5     Fase 6     Fase 7
SaaS Fnd  Multi-VCS  Obs Hub    FinOps    Biz Prod   AI Agents  Proj Mgmt
```

---

### Fase 1 — Foundation SaaS (Q1 2026)

**Motivação:** Transformar o Titlis Operator em uma plataforma SaaS multi-tenant, isolando dados por tenant e removendo acesso direto ao banco de dados.

**Novas Abstrações:**
- `TitlisAPIClient` — comunicação HTTP + UDP com a API central
- `TenantMiddleware` — isolamento de tenant em todas as queries

**Novas ENV vars:**
```bash
TENANT_ID=uuid-here
TENANT_SLUG=company-name
API_URL=https://api.titlis.io
API_KEY=titlis_api_key_here
TENANT_MAX_CLUSTERS=5
TENANT_MAX_WORKLOADS=100
API_HTTP_TIMEOUT_SECONDS=30
API_HTTP_MAX_RETRIES=3
API_UDP_ENABLED=true
API_UDP_HOST=telemetry.titlis.io
API_UDP_PORT=8125
API_UDP_BATCH_SIZE=100
API_UDP_FLUSH_INTERVAL_SECONDS=10
```

**Impacto nas Regras:**
- Nova regra: toda query ao banco deve respeitar RLS policies de `tenant_id`
- Nova regra: Operator não acessa banco diretamente — apenas via API HTTP
- Nova regra: fallback local via CRD quando API indisponível

---

### Fase 2 — Multi-VCS Integration (Q2 2026)

**Motivação:** O `GitHubRepository` está acoplado diretamente ao GitHub. A abstração via `VCSPort` permitirá suporte a GitLab e Bitbucket sem alterar a lógica de remediação.

**Novas Abstrações:**
- `VCSPort` — substitui `GitHubPort`, com mesmos métodos generalizados
- `GitLabRepository` — adapter para GitLab API
- `BitbucketRepository` — adapter para Bitbucket API

**Novas ENV vars:**
```bash
GITLAB_ENABLED=false
GITLAB_TOKEN=glpat_...
GITLAB_BASE_BRANCH=main
GITLAB_URL=https://gitlab.com
BITBUCKET_ENABLED=false
BITBUCKET_USERNAME=...
BITBUCKET_APP_PASSWORD=...
BITBUCKET_WORKSPACE=...
VCS_DEFAULT_PROVIDER=github
VCS_TOKEN_ROTATION_ENABLED=true
VCS_TOKEN_FAILOVER_ENABLED=true
```

**Impacto nas Regras:**
- `GitHubPort` renomeado para `VCSPort` — atualizar todos os usos
- Nova regra: `RemediationService` depende de `VCSPort`, não `GitHubPort`
- Nova regra: VCS factory em `dependencies.py` seleciona provider via `VCS_DEFAULT_PROVIDER`

---

### Fase 3 — Observability Hub (Q3 2026)

**Motivação:** Clientes usam diferentes provedores de observabilidade. A abstração via `ObservabilityPort` permite coleta de métricas de CPU/mem de qualquer provedor.

**Novas Abstrações:**
- `ObservabilityPort` — substitui `DatadogPort` para coleta de métricas de container
- `DynatraceAdapter`, `PrometheusAdapter`, `NewRelicAdapter`
- Query translation layer — traduz queries de domínio para DSL específico de cada provedor

**Novas ENV vars:**
```bash
DT_ENABLED=false
DT_API_TOKEN=...
DT_ENVIRONMENT=https://abc123.live.dynatrace.com
PROM_ENABLED=false
PROM_URL=http://prometheus.monitoring.svc:9090
NR_ENABLED=false
NR_API_KEY=...
NR_ACCOUNT_ID=123456
OBS_DEFAULT_PROVIDER=datadog
OBS_QUERY_CACHE_TTL_SECONDS=300
```

**Impacto nas Regras:**
- `DatadogPort` separado em `ObservabilityPort` (métricas) + `SLOPort` (SLOs)
- Nova regra: `RemediationService` usa `ObservabilityPort`, não `DatadogPort` diretamente
- Query cache obrigatório para evitar rate limiting nos provedores

---

### Fase 4 — FinOps Integration (Q4 2026)

**Motivação:** Adicionar o pilar `COST` ao scorecard, com dados reais de gasto de cloud por workload.

**Novas Abstrações:**
- `CostPort` — interface para consulta de custo por workload/namespace
- `AWSCostExplorerAdapter`, `GCPBillingAdapter`, `AzureCostAdapter`

**Novas ENV vars:**
```bash
AWS_COST_ENABLED=false
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
GCP_COST_ENABLED=false
GCP_SERVICE_ACCOUNT_KEY=...
GCP_BILLING_ACCOUNT_ID=...
AZURE_COST_ENABLED=false
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_TENANT_ID=...
AZURE_SUBSCRIPTION_ID=...
COST_SCORE_ENABLED=false
COST_SCORE_WEIGHT=1.5
COST_THRESHOLD_WARNING=50
COST_THRESHOLD_CRITICAL=30
```

**Impacto nas Regras:**
- Novo pilar `COST` no enum `ValidationPillar`
- Novas regras de validação: custo por réplica, custo vs utilização, custo total vs tier
- `ScorecardService` passa a aceitar `CostPort` opcionalmente

---

### Fase 5 — Business Products (Q1 2027)

**Motivação:** Stakeholders de negócio precisam de visibilidade sobre saúde dos produtos, não apenas workloads técnicos. Esta fase mapeia Deployments em Produtos de Negócio com health score agregado.

**Novas Abstrações:**
- `BusinessProduct` — domain model
- `ProductDiscoveryService` — 5 estratégias de descoberta:
  1. **cascade** — tenta todas as estratégias em ordem
  2. **backstage** — via Backstage catalog API
  3. **tags** — via labels Kubernetes (`product`, `app.kubernetes.io/part-of`)
  4. **manifest** — via anotações no deploy.yaml
  5. **labels** — via Datadog service tags

**Novas ENV vars:**
```bash
PRODUCT_DISCOVERY_ENABLED=true
PRODUCT_DISCOVERY_STRATEGY=cascade
PRODUCT_DEFAULT_NAME=unclassified
PRODUCT_DEFAULT_TEAM=platform-engineering
PRODUCT_AGGREGATE_STRATEGY=weighted
PRODUCT_TIER_WEIGHT_T1=4.0
PRODUCT_TIER_WEIGHT_T2=3.0
PRODUCT_TIER_WEIGHT_T3=2.0
PRODUCT_TIER_WEIGHT_T4=1.0
```

**Impacto nas Regras:**
- Nova entidade `BusinessProduct` no domínio — sem dependência de infraestrutura
- `ScorecardEnricher` pode enriquecer com dados de produto
- Novo CRD `ProductHealth` para persistir health score agregado por produto

---

### Fase 6 — AI Agents (Q2 2027)

**Motivação:** Automatizar a geração de sugestões de remediação mais complexas e predição de riscos usando LLMs, com safety checks obrigatórios.

**Novas Abstrações:**
- `AIAgentService` — framework base para agentes
- `LLMGateway` — abstração multi-provider (OpenAI, Anthropic, Gemini, local)
- `AISafetyChecker` — valida sugestões antes de aplicar

**Novas ENV vars:**
```bash
AI_ENABLED=false
AI_LLM_PROVIDER=openai
AI_OPENAI_API_KEY=...
AI_ANTHROPIC_API_KEY=...
AI_GEMINI_API_KEY=...
AI_LOCAL_MODEL_PATH=...
AI_SAFETY_CHECK_ENABLED=true
AI_MAX_SUGGESTIONS_PER_HOUR=50
AI_REQUIRE_HUMAN_REVIEW=true
AI_ALLOWED_FILE_PATTERNS=*.yaml,*.yml,*.json
AI_DEBT_AGENT_ENABLED=true
AI_REMEDIATION_AGENT_ENABLED=true
AI_RISK_AGENT_ENABLED=false
```

**Impacto nas Regras:**
- Nova regra: `AI_REQUIRE_HUMAN_REVIEW=true` obrigatório em produção
- Nova regra: `AI_ALLOWED_FILE_PATTERNS` limita quais arquivos podem ser modificados por agentes
- Nova regra: `AISafetyChecker` executado antes de qualquer commit automático via agente
- O `AISafetyChecker` é uma Port — implementações podem variar

---

### Fase 7 — Project Management Integration (Q3 2027)

**Motivação:** Transformar issues detectadas pelo scorecard em tickets rastreáveis nos sistemas de PM da equipe, fechando o loop operacional.

**Novas Abstrações:**
- `ProjectManagementPort` — interface para criação de tickets
- `JiraAdapter`, `LinearAdapter`

**Novas ENV vars:**
```bash
JIRA_ENABLED=false
JIRA_URL=https://company.atlassian.net
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=TECH
JIRA_ISSUE_TYPE=Technical Debt
LINEAR_ENABLED=false
LINEAR_API_KEY=...
LINEAR_TEAM_ID=abc123
PM_AUTO_CREATE_TICKETS=false
PM_SEVERITY_THRESHOLD=ERROR
PM_REQUIRE_HUMAN_APPROVAL=true
```

**Impacto nas Regras:**
- Nova regra: `PM_REQUIRE_HUMAN_APPROVAL=true` obrigatório para criação automática de tickets
- `ScorecardController` notifica `ProjectManagementService` após avaliação quando `PM_AUTO_CREATE_TICKETS=true`
- Deduplicação de tickets: verificar ticket aberto antes de criar novo (mesmo padrão do PR dedup)

---

## Parte III — Design Patterns Planejados (Futuros)

Complementam os 14 patterns atuais documentados no CLAUDE.md.

### P-15: VCS Abstraction Factory (Fase 2)
`dependencies.py` usa `VCS_DEFAULT_PROVIDER` para selecionar qual adapter retornar via factory. Permite troca de provider sem alterar lógica de remediação.

### P-16: Observability Query Translation (Fase 3)
Queries de domínio (ex: `get_cpu_avg_last_24h(service, namespace)`) são traduzidas para DSL específico de cada provedor por um `QueryTranslator`. Resultado normalizado antes de retornar ao domínio.

### P-17: Product Discovery Strategy Chain (Fase 5)
`ProductDiscoveryService` implementa Chain of Responsibility: cada estratégia tenta descobrir o produto e passa para a próxima se falhar. Estratégia `cascade` executa todas em ordem configurável.

### P-18: AI Safety Gate (Fase 6)
Toda sugestão de agente AI passa por `AISafetyChecker` antes de ser aplicada. Gate verifica: arquivo permitido, tamanho do diff, ausência de credenciais, e opcionalmente exige aprovação humana.

### P-19: PM Ticket Deduplication (Fase 7)
Antes de criar ticket, `ProjectManagementService` busca tickets abertos com mesmo `rule_id + namespace + resource`. Mesmo padrão do `find_open_remediation_pr` já implementado.

---

## Referências Cruzadas

| Documento | Conteúdo |
|-----------|----------|
| [CLAUDE.md](../CLAUDE.md) | Instrução principal — regras, arquitetura, stack, variáveis de ambiente |
| [evolution-checklist.md](./evolution-checklist.md) | Checklist de progresso por fase (marcar itens concluídos) |
| [guia-extensao-scorecard.md](./guia-extensao-scorecard.md) | Como adicionar novas regras de scorecard |
| [modelagem-dados-new.md](./modelagem-dados-new.md) | Modelo relacional com SCD Type 4 e métricas append-only |
