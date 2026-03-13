# Scorecard — Regras de Validação

> Fonte de verdade: `src/application/services/scorecard_service.py` → `_get_default_rules()`
> Configuração por env: `config/scorecard-config.yaml`

---

## Resumo por Pilar

| Pilar | Regras | Peso Total | Remediáveis |
|-------|--------|-----------|-------------|
| RESILIENCE | 16 | 101.0 | RES-003, RES-004, RES-005, RES-006, RES-007, RES-008 |
| SECURITY | 4 | 28.0 | — |
| PERFORMANCE | 3 | 10.0 | PERF-001, PERF-002 |
| OPERATIONAL | 1 | 8.0 | — |
| **Total** | **24** | **147.0** | **8** |

---

## RESILIENCE

| ID | Nome | Tipo | Severidade | Peso | Applies To | Remediável | Perfil |
|----|------|------|-----------|------|-----------|-----------|--------|
| RES-001 | Liveness Probe Configurada | BOOLEAN | ERROR | 10.0 | todos | Não | — |
| RES-002 | Readiness Probe Configurada | BOOLEAN | ERROR | 10.0 | todos | Não | — |
| RES-003 | CPU Requests Definidos | BOOLEAN | ERROR | 8.0 | todos | **Sim** ⚡ | — |
| RES-004 | CPU Limits Definidos | BOOLEAN | WARNING | 5.0 | todos | **Sim** ⚡ | — |
| RES-005 | Memory Requests Definidos | BOOLEAN | ERROR | 8.0 | todos | **Sim** ⚡ | — |
| RES-006 | Memory Limits Definidos | BOOLEAN | WARNING | 5.0 | todos | **Sim** ⚡ | — |
| RES-007 | HPA Configurado | BOOLEAN | WARNING | 7.0 | Deployment | **Sim** ⚡ | — |
| RES-008 | HPA com Métricas | BOOLEAN | WARNING | 5.0 | Deployment | **Sim** ⚡ | — |
| RES-009 | Graceful Shutdown Configurado | BOOLEAN | INFO | 3.0 | todos | Não | — |
| RES-010 | Container Non-Root | BOOLEAN | ERROR | 10.0 | todos | Não | — |
| RES-011 | Pod Security Context | BOOLEAN | WARNING | 5.0 | todos | Não | — |
| RES-012 | NetworkPolicy Aplicada | BOOLEAN | WARNING | 7.0 | Deployment, StatefulSet | Não | — |
| RES-013 | Replicas Mínimas (>= 2) | NUMERIC | WARNING | 6.0 | Deployment | Não | — |
| RES-014 | Estratégia de Rollout | BOOLEAN | WARNING | 4.0 | Deployment | Não | — |
| RES-016 | HPA MinReplicas >= 2 | NUMERIC | WARNING | 5.0 | Deployment | Não | leve |
| RES-017 | HPA ScaleUp Stabilization == 0s | NUMERIC | WARNING | 4.0 | Deployment | Não | rígido |
| RES-018 | HPA ScaleDown Stabilization >= 300s | NUMERIC | WARNING | 4.0 | Deployment | Não | rígido |
| RES-019 | HPA com Políticas Explícitas | BOOLEAN | WARNING | 4.0 | Deployment | Não | rígido |

> **Nota:** RES-015 é definível via `scorecard-config.yaml` (PodDisruptionBudget). Não está no default.

---

## SECURITY

| ID | Nome | Tipo | Severidade | Peso | Pattern/Valor | Remediável |
|----|------|------|-----------|------|--------------|-----------|
| SEC-001 | Imagem com Tag Específica | REGEX | ERROR | 9.0 | `^(?!.*:latest$).+$` | Não |
| SEC-002 | ReadOnly Root Filesystem | BOOLEAN | WARNING | 6.0 | — | Não |
| SEC-003 | Privilege Escalation Desabilitado | BOOLEAN | ERROR | 8.0 | — | Não |
| SEC-004 | Capabilities Reduzidas | BOOLEAN | WARNING | 5.0 | — | Não |

---

## PERFORMANCE

| ID | Nome | Tipo | Severidade | Peso | Faixa Válida | Remediável | Perfil |
|----|------|------|-----------|------|-------------|-----------|--------|
| PERF-001 | Resource Limits Adequados | NUMERIC | WARNING | 4.0 | limits ≤ 3× requests | **Sim** ⚡ | — |
| PERF-002 | HPA com Target Adequado | NUMERIC | INFO | 3.0 | 50% – 90% | **Sim** ⚡ | — |
| PERF-003 | HPA CPU Target <= 70% | NUMERIC | INFO | 3.0 | ≤ 70% | Não | leve |

---

## Detalhamento por Regra

### RES-001 — Liveness Probe Configurada
- **Caminho K8s:** `spec.template.spec.containers[0].livenessProbe`
- **O que valida:** Presença de livenessProbe no container principal
- **Remediação manual:** Adicione `livenessProbe` para detectar containers travados
- **Docs:** https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

### RES-002 — Readiness Probe Configurada
- **Caminho K8s:** `spec.template.spec.containers[0].readinessProbe`
- **O que valida:** Presença de readinessProbe no container principal
- **Remediação manual:** Adicione `readinessProbe` para controle de tráfego
- **Docs:** https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

---

### RES-003 — CPU Requests Definidos ⚡
- **Caminho K8s:** `spec.template.spec.containers[0].resources.requests.cpu`
- **Categoria remediação:** `resources`
- **Feature flag:** `ENABLE_REMEDIATION_RESOURCES=true`
- **Auto-remediação — o que o PR faz:**
  - Lê `resources.requests.cpu` atual do `manifests/kubernetes/main/deploy.yaml`
  - Calcula valor sugerido: média CPU do Datadog × 1.2 (se disponível) ou `REMEDIATION_DEFAULT_CPU_REQUEST` (default: `100m`)
  - Aplica `max(atual, sugerido)` — nunca reduz valor existente
  - Escreve o novo valor em `spec.template.spec.containers[0].resources.requests.cpu`

### RES-004 — CPU Limits Definidos ⚡
- **Caminho K8s:** `spec.template.spec.containers[0].resources.limits.cpu`
- **Categoria remediação:** `resources`
- **Feature flag:** `ENABLE_REMEDIATION_RESOURCES=true`
- **Auto-remediação — o que o PR faz:**
  - Calcula valor sugerido: média CPU do Datadog × 2.0 (se disponível) ou `REMEDIATION_DEFAULT_CPU_LIMIT` (default: `500m`)
  - Aplica `max(atual, sugerido)` em `resources.limits.cpu`

### RES-005 — Memory Requests Definidos ⚡
- **Caminho K8s:** `spec.template.spec.containers[0].resources.requests.memory`
- **Categoria remediação:** `resources`
- **Feature flag:** `ENABLE_REMEDIATION_RESOURCES=true`
- **Auto-remediação — o que o PR faz:**
  - Calcula valor sugerido: média memória do Datadog × 1.2 (se disponível) ou `REMEDIATION_DEFAULT_MEMORY_REQUEST` (default: `128Mi`)
  - Aplica `max(atual, sugerido)` em `resources.requests.memory`

### RES-006 — Memory Limits Definidos ⚡
- **Caminho K8s:** `spec.template.spec.containers[0].resources.limits.memory`
- **Categoria remediação:** `resources`
- **Feature flag:** `ENABLE_REMEDIATION_RESOURCES=true`
- **Auto-remediação — o que o PR faz:**
  - Calcula valor sugerido: média memória do Datadog × 1.5 (se disponível) ou `REMEDIATION_DEFAULT_MEMORY_LIMIT` (default: `512Mi`)
  - Aplica `max(atual, sugerido)` em `resources.limits.memory`

---

### RES-007 — HPA Configurado ⚡
- **Verificação:** `_check_hpa_exists(namespace, name)` — lista HPAs no namespace e verifica `scaleTargetRef.name`
- **Categoria remediação:** `hpa`
- **Feature flag:** `ENABLE_REMEDIATION_HPA=true`
- **Auto-remediação — o que o PR faz:**
  - **HPA não existe (hpa-create):** Acrescenta ao `deploy.yaml` um documento YAML separado com `kind: HorizontalPodAutoscaler` contendo:
    - `minReplicas`: `REMEDIATION_HPA_MIN_REPLICAS` (default: `2`)
    - `maxReplicas`: `REMEDIATION_HPA_MAX_REPLICAS` (default: `10`)
    - Métricas CPU (`averageUtilization: REMEDIATION_HPA_CPU_UTILIZATION`, default: `70%`)
    - Métricas memória (`averageUtilization: REMEDIATION_HPA_MEMORY_UTILIZATION`, default: `80%`)
    - Annotations: `titlis.io/auto-generated: "true"`, `titlis.io/generated-by: titlis-operator-remediation`
  - **Perfil RIGID** (annotation `titlis.io/criticality: high` ou requests Datadog > threshold): adiciona bloco `behavior` com `scaleUp.stabilizationWindowSeconds: 0` e `scaleDown.stabilizationWindowSeconds: 300`
  - **HPA existe (hpa-update):** atualiza conforme RES-008

### RES-008 — HPA com Métricas ⚡
- **Verificação:** `_check_hpa_metrics(namespace, name)` — verifica se `spec.metrics` está populado
- **Categoria remediação:** `hpa`
- **Feature flag:** `ENABLE_REMEDIATION_HPA=true`
- **Auto-remediação — o que o PR faz:**
  - **HPA existe (hpa-update):** Atualiza o documento HPA existente no `deploy.yaml`:
    - `minReplicas`: `max(atual, REMEDIATION_HPA_MIN_REPLICAS)` — nunca desce
    - `maxReplicas`: `max(atual, REMEDIATION_HPA_MAX_REPLICAS)` — nunca desce
    - CPU utilization target: `min(atual, REMEDIATION_HPA_CPU_UTILIZATION)` — escala mais cedo = melhor
    - Memory utilization target: `min(atual, REMEDIATION_HPA_MEMORY_UTILIZATION)`
    - Substitui `spec.metrics` com CPU + memory resources
  - **Perfil RIGID:** também atualiza/adiciona `spec.behavior` com políticas de scaleUp/scaleDown

---

### RES-009 — Graceful Shutdown Configurado
- **Caminho K8s:** `spec.template.spec.terminationGracePeriodSeconds`
- **Remediação manual:** Configure `terminationGracePeriodSeconds` para shutdown gracioso

### RES-010 — Container Non-Root
- **Caminho K8s:** `spec.template.spec.containers[0].securityContext.runAsNonRoot`
- **Remediação manual:** Configure `securityContext.runAsNonRoot: true`

### RES-011 — Pod Security Context
- **Caminho K8s:** `spec.template.spec.securityContext`
- **Remediação manual:** Configure `securityContext` no nível do pod

### RES-012 — NetworkPolicy Aplicada
- **Verificação:** `_check_network_policy_exists(namespace, name)` — busca NetworkPolicies com `podSelector` compatível
- **Remediação manual:** Crie NetworkPolicy para limitar tráfego de rede

### RES-013 — Replicas Mínimas
- **Caminho K8s:** `spec.replicas`
- **Condição:** `replicas >= 2`
- **Remediação manual:** Aumente replicas para pelo menos 2 para alta disponibilidade

### RES-014 — Estratégia de Rollout
- **Caminho K8s:** `spec.strategy`
- **Remediação manual:** Configure `strategy.type: RollingUpdate` com `maxUnavailable`/`maxSurge`

### RES-016 — HPA MinReplicas >= 2 _(perfil leve)_
- **Verificação:** `_get_hpa_min_replicas(namespace, name)`
- **Condição:** `minReplicas >= 2`
- **Remediação manual:** Configure `HPA minReplicas >= 2`

### RES-017 — HPA ScaleUp Stabilization == 0s _(perfil rígido)_
- **Verificação:** `_get_hpa_scale_up_stabilization(namespace, name)`
- **Condição:** `behavior.scaleUp.stabilizationWindowSeconds == 0`
- **Remediação manual:** Configure `behavior.scaleUp.stabilizationWindowSeconds: 0`

### RES-018 — HPA ScaleDown Stabilization >= 300s _(perfil rígido)_
- **Verificação:** `_get_hpa_scale_down_stabilization(namespace, name)`
- **Condição:** `behavior.scaleDown.stabilizationWindowSeconds >= 300`
- **Remediação manual:** Configure `behavior.scaleDown.stabilizationWindowSeconds: 300`

### RES-019 — HPA com Políticas Explícitas _(perfil rígido)_
- **Verificação:** `_check_hpa_behavior_policies(namespace, name)` — verifica presença de `scaleUp.policies` e `scaleDown.policies`
- **Remediação manual:** Configure `behavior.scaleUp.policies` e `behavior.scaleDown.policies`

---

### SEC-001 — Imagem com Tag Específica
- **Caminho K8s:** `spec.template.spec.containers[0].image`
- **Regex:** `^(?!.*:latest$).+$` — rejeita qualquer imagem terminada em `:latest`
- **Remediação manual:** Use tags versionadas (ex: `v1.2.3`) ao invés de `latest`

### SEC-002 — ReadOnly Root Filesystem
- **Caminho K8s:** `spec.template.spec.containers[0].securityContext.readOnlyRootFilesystem`
- **Remediação manual:** Configure `securityContext.readOnlyRootFilesystem: true`

### SEC-003 — Privilege Escalation Desabilitado
- **Caminho K8s:** `spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation`
- **Condição:** campo deve ser `false`
- **Remediação manual:** Configure `securityContext.allowPrivilegeEscalation: false`

### SEC-004 — Capabilities Reduzidas
- **Caminho K8s:** `spec.template.spec.containers[0].securityContext.capabilities.drop`
- **Remediação manual:** `securityContext.capabilities.drop: ['ALL']`

---

### PERF-001 — Resource Limits Adequados ⚡
- **Verificação:** `_calculate_limit_ratio(resource)` — calcula `limit / request` para CPU e memória
- **Condição:** razão `<= 3.0`
- **Categoria remediação:** `resources`
- **Feature flag:** `ENABLE_REMEDIATION_RESOURCES=true`
- **Auto-remediação — o que o PR faz:**
  - Aciona a mesma ação de `resources` que RES-003 a RES-006: ajusta todos os requests e limits com `_keep_max(atual, sugerido)`
  - A razão limit/request é indiretamente corrigida porque os valores são recalculados a partir das métricas reais do Datadog (ou defaults proporcionais)
  - **Importante:** não há redução de limits — se o atual já está acima do sugerido, o valor é mantido

### PERF-002 — HPA com Target Adequado ⚡
- **Verificação:** `_get_hpa_target(namespace, name)` — lê `averageUtilization` das métricas do HPA
- **Condição:** `50% <= target <= 90%`
- **Categoria remediação:** `hpa`
- **Feature flag:** `ENABLE_REMEDIATION_HPA=true`
- **Auto-remediação — o que o PR faz:**
  - Se HPA existe: aplica `hpa-update` (mesmo fluxo de RES-008)
    - CPU target: `min(atual, REMEDIATION_HPA_CPU_UTILIZATION)` — preserva se já é mais agressivo que o default
    - Memory target: `min(atual, REMEDIATION_HPA_MEMORY_UTILIZATION)`
  - Se HPA não existe: aplica `hpa-create` (mesmo fluxo de RES-007)

### PERF-003 — HPA CPU Target <= 70% _(perfil leve)_
- **Verificação:** `_get_hpa_target(namespace, name)`
- **Condição:** `target <= 70%`
- **Remediação manual:** Reduza o target de CPU do HPA para <= 70%

---

## OPERATIONAL

| ID | Nome | Tipo | Severidade | Peso | Applies To | Remediável |
|----|------|------|-----------|------|-----------|-----------|
| OPS-001 | Instrumentação Datadog | BOOLEAN | WARNING | 8.0 | Deployment | Não |

---

### OPS-001 — Instrumentação Datadog
- **Verificação:** `_validate_ops_001(rule, resource, namespace, name)` — validador customizado
- **O que valida:**
  1. `metadata.labels` contém `tags.datadoghq.com/env`, `tags.datadoghq.com/service`, `tags.datadoghq.com/version` (não vazios)
  2. `spec.template.metadata.labels` contém os mesmos 3 labels + `admission.datadoghq.com/enabled: "true"`
  3. `spec.template.metadata.annotations` contém `admission.datadoghq.com/python-lib.version` com versão **> 3.17.2** (ex: `v4.5.3`)
- **Remediação manual:** Adicione as labels e annotations conforme exemplo:
  ```yaml
  metadata:
    labels:
      tags.datadoghq.com/env: production
      tags.datadoghq.com/service: my-service
      tags.datadoghq.com/version: "1.0.0"
  spec:
    template:
      metadata:
        labels:
          tags.datadoghq.com/env: production
          tags.datadoghq.com/service: my-service
          tags.datadoghq.com/version: "1.0.0"
          admission.datadoghq.com/enabled: "true"
        annotations:
          admission.datadoghq.com/python-lib.version: v4.5.3
  ```
- **Docs:** https://docs.datadoghq.com/tracing/trace_collection/library_injection_local/

---

## Regras Remediáveis por Categoria

A `RemediationService` agrupa issues por `category` para montar o PR:

| Categoria | Regras | Arquivo modificado | O que o PR faz |
|-----------|--------|--------------------|---------------|
| `resources` | RES-003, RES-004, RES-005, RES-006, PERF-001 | `manifests/kubernetes/main/deploy.yaml` | Atualiza `resources.requests` e `resources.limits` do container principal usando `_keep_max(atual, sugerido_datadog_ou_default)` |
| `hpa-create` | RES-007, RES-008, PERF-002 | `manifests/kubernetes/main/deploy.yaml` | Acrescenta documento YAML separado `---` com HPA completo (autoscaling/v2) |
| `hpa-update` | RES-007, RES-008, PERF-002 | `manifests/kubernetes/main/deploy.yaml` | Atualiza o documento HPA existente: minReplicas/maxReplicas com `max()`, utilization targets com `min()` |

> ⚡ = remediável automaticamente via PR

**Pré-condição obrigatória:** o Deployment deve ter `DD_GIT_REPOSITORY_URL` nas variáveis de ambiente apontando para o repositório GitHub que contém o `deploy.yaml`.

**Invariante de remediação — o operador nunca piora o que já existe:**
- CPU/memória requests/limits: `_keep_max(atual, sugerido, parser)` — retorna sempre o maior
- HPA `minReplicas` e `maxReplicas`: `max(atual, default)` — nunca desce
- HPA utilization target: `min(atual, default)` — target menor = escala mais cedo = mais agressivo (preserva se já é melhor)

**Perfis HPA:**

| Perfil | Como é detectado | Diferencial no PR |
|--------|-----------------|------------------|
| `LIGHT` (padrão) | Nenhuma annotation, requests baixos | HPA sem `behavior` |
| `RIGID` | Annotation `titlis.io/criticality: high` **ou** requests Datadog últimos 30 dias > threshold | HPA com `behavior.scaleUp.stabilizationWindowSeconds: 0` e `behavior.scaleDown.stabilizationWindowSeconds: 300` + políticas explícitas |

**Feature flags de remediação:**

| ENV var | Default | Controla |
|---------|---------|---------|
| `ENABLE_REMEDIATION_RESOURCES` | `true` | Ação `resources` (RES-003 a RES-006, PERF-001) |
| `ENABLE_REMEDIATION_HPA` | `true` | Ação `hpa-create` / `hpa-update` (RES-007, RES-008, PERF-002) |

---

## Configuração via `scorecard-config.yaml`

É possível sobrescrever qualquer regra default e adicionar regras customizadas:

```yaml
rules:
  # Sobrescrever peso ou severidade de uma regra existente
  - id: "RES-001"
    enabled: true
    weight: 15.0
    severity: "error"

  # Desabilitar uma regra
  - id: "RES-007"
    enabled: false

  # Adicionar regra customizada (ID não existente no default)
  - id: "RES-015"
    name: "Pod Disruption Budget"
    pillar: "resilience"
    type: "boolean"
    source: "K8s API"
    severity: "warning"
    weight: 6.0
    applies_to: ["Deployment"]
    description: "Deployment deve ter PodDisruptionBudget configurado"
    remediation: "Crie PodDisruptionBudget para alta disponibilidade durante manutenção"

notification_thresholds:
  critical: 65.0   # overall_score < 65 → notificação crítica
  error:    75.0
  warning:  85.0

excluded_namespaces:
  - "kube-system"
  - "kube-public"
  - "kube-node-lease"
  - "datadog"
  - "monitoring"
```

---

## Como Adicionar uma Nova Regra

Ver [guia-extensao-scorecard.md](guia-extensao-scorecard.md) para o passo-a-passo completo.

Resumo dos 4 passos:
1. Adicionar `ValidationRule` em `_get_default_rules()` com ID único no padrão `PILAR-NNN`
2. Implementar a lógica de verificação em `_get_rule_value()` (caminho K8s ou função `_check_*`)
3. Se remediável: declarar `category` na `RemediationIssue` e atualizar `remediation_service.py`
4. Adicionar teste em `tests/unit/test_services.py`
