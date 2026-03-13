# Titlis Operator — Evolution Checklist

> Documento de acompanhamento das fases de evolução planejadas no CLAUDE.md (Seções 12–18).
> Atualizar status ao iniciar/concluir cada item.

---

## Legenda de Status

| Símbolo | Significado |
|---------|-------------|
| `📋 Planned` | Planejado, não iniciado |
| `🚧 In Progress` | Em desenvolvimento |
| `✅ Done` | Concluído e testado |
| `⏸ Blocked` | Bloqueado por dependência |
| `❌ Cancelled` | Cancelado/descartado |

---

## Fase 1 — Foundation SaaS (Q1 2026)

**Objetivo:** Tornar o Titlis Operator multi-tenant e remover acesso direto ao banco de dados.

### Semana 1–2: Preparação Multi-Tenant

| Status | Item | Arquivo(s) Afetado(s) |
|--------|------|----------------------|
| `📋 Planned` | Adicionar `tenant_id` a todas as tabelas `titlis_oltp.*` | migrations/ |
| `📋 Planned` | Criar tabela `titlis_oltp.tenants` | migrations/ |
| `📋 Planned` | Implementar Row Level Security policies | migrations/ |
| `📋 Planned` | Atualizar `settings.py` com tenant configuration | src/settings.py |
| `📋 Planned` | Criar middleware de tenant isolation na API | src/infrastructure/api/ |
| `📋 Planned` | Testes de isolamento de tenant | tests/unit/, tests/integration/ |

### Semana 3–4: Operator → API Communication

| Status | Item | Arquivo(s) Afetado(s) |
|--------|------|----------------------|
| `📋 Planned` | Criar `TitlisAPIClient` em `src/infrastructure/api/` | src/infrastructure/api/client.py |
| `📋 Planned` | Implementar HTTP endpoints para scorecards | src/infrastructure/api/ |
| `📋 Planned` | Implementar UDP telemetry sender | src/infrastructure/api/ |
| `📋 Planned` | Remover acesso direto ao DB do Operator | src/bootstrap/dependencies.py |
| `📋 Planned` | Implementar retry com fallback local (CRD) | src/infrastructure/api/client.py |
| `📋 Planned` | Testes de comunicação com falhas de rede | tests/unit/ |

### Decisões Arquiteturais — Fase 1

| ID | Decisão | Status | Data Limite |
|----|---------|--------|-------------|
| AD-001 | Operator sem DB Direct Access | `📋 Pending` | 2026-02-15 |
| AD-002 | SCD Type 4 mantido para Multi-Tenant | `📋 Pending` | 2026-02-15 |
| AD-003 | HTTP + UDP para comunicação Operator-API | `📋 Pending` | 2026-02-28 |

### Métricas de Sucesso — Fase 1

| Métrica | Target | Status |
|---------|--------|--------|
| Tenant Isolation | 100% — Testes de penetração entre tenants | `📋 Not Measured` |
| API Latency (p95) | < 100ms — Datadog APM | `📋 Not Measured` |
| Operator Memory | < 256MB — Kubernetes metrics | `📋 Not Measured` |
| DB Query Time (OLTP) | < 10ms — PostgreSQL slow query log | `📋 Not Measured` |

---

## Fase 2 — Multi-VCS Integration (Q2 2026)

**Objetivo:** Abstrair a integração com VCS para suportar GitHub, GitLab e Bitbucket.

### Semana 5–6: VCS Abstraction

| Status | Item | Arquivo(s) Afetado(s) |
|--------|------|----------------------|
| `📋 Planned` | Criar `VCSPort` em `src/application/ports/vcs_port.py` | src/application/ports/vcs_port.py |
| `📋 Planned` | Refatorar `GitHubRepository` para implementar VCSPort | src/infrastructure/github/repository.py |
| `📋 Planned` | Criar `GitLabRepository` adapter | src/infrastructure/gitlab/repository.py |
| `📋 Planned` | Implementar multi-token support | src/infrastructure/github/, src/infrastructure/gitlab/ |
| `📋 Planned` | Atualizar `dependencies.py` com VCS factory | src/bootstrap/dependencies.py |
| `📋 Planned` | Testes de integração para ambos providers | tests/integration/ |

### Features F2

| ID | Feature | Status | Dependência |
|----|---------|--------|-------------|
| F2-01 | VCSPort abstraction layer | `📋 Planned` | CLAUDE.md §5 (Ports) |
| F2-02 | GitLabRepository adapter | `📋 Planned` | F2-01 |
| F2-03 | BitbucketRepository adapter | `📋 Planned` | F2-01 |
| F2-04 | Multi-token support with failover | `📋 Planned` | F2-01 |
| F2-05 | Provider selection via config | `📋 Planned` | F2-01 |

### Métricas de Sucesso — Fase 2

| Métrica | Target | Status |
|---------|--------|--------|
| PR Creation Success | > 99% — app_remediations.status | `📋 Not Measured` |
| Token Failover Time | < 5s — Logs de troca de token | `📋 Not Measured` |
| Provider Switch Config | < 1min — Config change time | `📋 Not Measured` |

---

## Fase 3 — Observability Hub (Q3 2026)

**Objetivo:** Abstrair provedores de observabilidade (Datadog, Dynatrace, Prometheus, New Relic).

| ID | Feature | Status | Dependência |
|----|---------|--------|-------------|
| F3-01 | ObservabilityPort abstraction | `📋 Planned` | CLAUDE.md §5 (Ports) |
| F3-02 | DynatraceAdapter | `📋 Planned` | F3-01 |
| F3-03 | PrometheusAdapter | `📋 Planned` | F3-01 |
| F3-04 | NewRelicAdapter | `📋 Planned` | F3-01 |
| F3-05 | Query translation layer | `📋 Planned` | F3-01 |

### Métricas de Sucesso — Fase 3

| Métrica | Target | Status |
|---------|--------|--------|
| Query Translation Accuracy | 100% — Testes de queries | `📋 Not Measured` |
| Provider Switch Time | < 30s — Config reload time | `📋 Not Measured` |
| Metric Collection Latency | < 60s — Datadog → Titlis delay | `📋 Not Measured` |

---

## Fase 4 — FinOps Integration (Q4 2026)

**Objetivo:** Integrar custo de cloud (AWS, GCP, Azure) ao scorecard via pilar COST.

| ID | Feature | Status | Dependência |
|----|---------|--------|-------------|
| F4-01 | CostPort abstraction | `📋 Planned` | CLAUDE.md §5 (Ports) |
| F4-02 | AWSCostExplorerAdapter | `📋 Planned` | F4-01 |
| F4-03 | GCPBillingAdapter | `📋 Planned` | F4-01 |
| F4-04 | AzureCostAdapter | `📋 Planned` | F4-01 |
| F4-05 | COST pillar in scorecard | `📋 Planned` | F4-01 |

---

## Fase 5 — Business Products (Q1 2027)

**Objetivo:** Mapear workloads Kubernetes em produtos de negócio com health score agregado.

### Semana 7–8: Product Concept

| Status | Item | Arquivo(s) Afetado(s) |
|--------|------|----------------------|
| `📋 Planned` | Criar `BusinessProduct` domain model | src/domain/ |
| `📋 Planned` | Implementar `ProductDiscoveryService` | src/application/services/ |
| `📋 Planned` | Adicionar tabela `titlis_oltp.business_products` | migrations/ |
| `📋 Planned` | Adicionar tabela `titlis_oltp.product_applications` | migrations/ |
| `📋 Planned` | Implementar 5 estratégias de descoberta | src/application/services/ |
| `📋 Planned` | Views agregadas por produto | migrations/ |

| ID | Feature | Status | Dependência |
|----|---------|--------|-------------|
| F5-01 | BusinessProduct domain model | `📋 Planned` | Architecture Doc §8 |
| F5-02 | ProductDiscoveryService | `📋 Planned` | F5-01 |
| F5-03 | Product-Application mapping (5 strategies) | `📋 Planned` | F5-02 |
| F5-04 | Aggregated health score calculation | `📋 Planned` | F5-01 |
| F5-05 | Executive dashboard views | `📋 Planned` | F5-04 |

---

## Fase 6 — AI Agents (Q2 2027)

**Objetivo:** Incorporar agentes de IA para sugestão de remediação e predição de risco.

| ID | Feature | Status | Dependência |
|----|---------|--------|-------------|
| F6-01 | AIAgentService framework | `📋 Planned` | Architecture Doc §10 |
| F6-02 | LLM Gateway (multi-provider) | `📋 Planned` | F6-01 |
| F6-03 | Remediation suggestion agent | `📋 Planned` | F6-02 |
| F6-04 | Risk prediction agent | `📋 Planned` | F6-02 |
| F6-05 | AI safety checker | `📋 Planned` | F6-01 |

### Decisão Pendente

| ID | Decisão | Status | Data Limite |
|----|---------|--------|-------------|
| AD-004 | LLM Provider padrão para AI Agents | `📋 Pending` | 2026-06-30 |

---

## Fase 7 — Project Management Integration (Q3 2027)

**Objetivo:** Criação automática de tickets técnicos a partir de issues detectadas.

| ID | Feature | Status | Dependência |
|----|---------|--------|-------------|
| F7-01 | ProjectManagementPort abstraction | `📋 Planned` | CLAUDE.md §5 (Ports) |
| F7-02 | JiraAdapter | `📋 Planned` | F7-01 |
| F7-03 | LinearAdapter | `📋 Planned` | F7-01 |
| F7-04 | Auto-ticket creation from issues | `📋 Planned` | F7-01 |
| F7-05 | Technical OKR generation | `📋 Planned` | F7-01 |

---

## Riscos Globais

| Risco | Impacto | Probabilidade | Mitigação | Status |
|-------|---------|---------------|-----------|--------|
| Tenant data leak | Crítico | Baixa | RLS policies + testes automatizados + audit logs | `📋 Not Mitigated` |
| API single point of failure | Alto | Média | Multi-AZ + circuit breaker + local CRD fallback | `📋 Not Mitigated` |
| VCS API rate limiting | Médio | Alta | Token rotation + request batching + exponential backoff | `📋 Not Mitigated` |
| AI safety violations | Alto | Média | Human review mandatory + safety checker + allowed file patterns | `📋 Not Mitigated` |
| DB performance degradation | Alto | Média | Partitioning strategy + query optimization + read replicas | `📋 Not Mitigated` |

### Decisão Pendente

| ID | Decisão | Status | Data Limite |
|----|---------|--------|-------------|
| AD-005 | Estratégia de particionamento para scale | `📋 Pending` | 2026-03-31 |

---

## Atualizações Obrigatórias no CLAUDE.md por Fase

Após cada fase implementada, atualizar as seguintes seções:

- [ ] **Seção 3** — Variáveis de Ambiente (novas ENV vars)
- [ ] **Seção 5** — Serviços, Jobs e Models (novos domain models e services)
- [ ] **Seção 7** — Design Patterns (novos padrões identificados)
- [ ] **Seção 11** — Regras Obrigatórias (novas regras de código)
- [ ] **Seção 4** — Estrutura de Diretórios (novos diretórios)
- [ ] **Seção 12** — Roadmap (atualizar status das features)
- [ ] **Este documento** — Marcar itens como `✅ Done`
