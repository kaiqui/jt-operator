# Guia de Extensão do Scorecard

> Como adicionar novas validações ao sistema de scorecard do Titlis Operator — do caso mais simples ao mais complexo, incluindo integração com ferramentas externas.

---

## Índice

1. [Conceitos Fundamentais](#1-conceitos-fundamentais)
2. [Antes de Começar — Mapa dos Arquivos](#2-antes-de-começar--mapa-dos-arquivos)
3. [Abordagem 1 — Via YAML (sem código)](#3-abordagem-1--via-yaml-sem-código)
4. [Abordagem 2 — Regra Simples em Código](#4-abordagem-2--regra-simples-em-código)
5. [Abordagem 3 — Regra com Função Personalizada](#5-abordagem-3--regra-com-função-personalizada)
6. [Abordagem 4 — Regra com Integração Externa](#6-abordagem-4--regra-com-integração-externa)
7. [Adicionando um Novo Pilar](#7-adicionando-um-novo-pilar)
8. [Referência Rápida dos Tipos de Regra](#8-referência-rápida-dos-tipos-de-regra)
9. [Como Escolher Peso e Severidade](#9-como-escolher-peso-e-severidade)
10. [Escrevendo Testes](#10-escrevendo-testes)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Conceitos Fundamentais

### O que é uma Validação?

Cada validação é uma **regra** (`ValidationRule`) que inspeciona um atributo de um workload Kubernetes e retorna **aprovado** ou **reprovado**. O conjunto de resultados forma o scorecard.

```
ValidationRule  →  _validate_rule()  →  ValidationResult
     (definição)      (execução)           (aprovado/reprovado + peso)
```

### Os 4 Tipos de Regra

| Tipo | Quando usar | Exemplo |
|---|---|---|
| `BOOLEAN` | Checar se algo existe ou é verdadeiro | Probe configurada? |
| `NUMERIC` | Checar se um número está em uma faixa | Réplicas ≥ 2? |
| `ENUM` | Checar se um valor está numa lista permitida | Ambiente é prod ou staging? |
| `REGEX` | Checar se um string segue um padrão | Imagem sem `:latest`? |

### As 2 Formas de Implementar a Lógica

| Forma | Quando usar | Como |
|---|---|---|
| **Mapeamento de caminho** | Valor existe em campo fixo do recurso K8s | Adicionar ao dicionário `rule_paths` |
| **Função personalizada** | Lógica complexa, consulta a APIs externas, cálculos | Criar método `_validate_{rule_id}()` |

---

## 2. Antes de Começar — Mapa dos Arquivos

```
src/
├── domain/models.py                         ← (1) Definição dos tipos (raramente mexer)
├── application/services/scorecard_service.py ← (2) Motor de validação — PRINCIPAL
│   ├── _get_default_rules()                  ←   Adicione a nova regra AQUI
│   ├── _extract_value_from_resource()        ←   Adicione o caminho K8s AQUI
│   └── _validate_{seu_id}()                 ←   Crie este método para lógica complexa
└── ...

config/
└── scorecard-config.yaml                    ← (3) Alternativa sem código (YAML)
```

---

## 3. Abordagem 1 — Via YAML (sem código)

**Quando usar:** Regras simples que verificam campos padrão do Kubernetes e que não precisam de lógica personalizada.

**Limitação:** Só funciona para os tipos `BOOLEAN`, `NUMERIC`, `ENUM`, `REGEX` em caminhos dot-notation já suportados.

**Exemplo: Exigir label `team` em todos os Deployments**

Edite `config/scorecard-config.yaml`:

```yaml
rules:
  # Ajuste de regra existente (opcional)
  - id: "RES-001"
    weight: 15.0           # Aumenta peso da liveness probe

  # Nova regra customizada
  - id: "COMP-001"
    name: "Label 'team' obrigatório"
    pillar: "compliance"
    type: "boolean"
    severity: "error"
    weight: 8.0
    applies_to:
      - "Deployment"
      - "StatefulSet"
    description: "Todo workload deve ter o label 'team' definido"
    remediation: "Adicione 'labels.team: nome-do-time' no metadata.labels"
    documentation_url: "https://wiki.interna/padroes-kubernetes"
```

**Atenção:** Para que a extração funcione automaticamente via YAML, o campo `id` da nova regra precisa ter um **caminho mapeado** em `_extract_value_from_resource()`. Regras adicionadas só por YAML sem caminho mapeado retornam `None` (reprovado).

Para o caso acima (`COMP-001`), você ainda precisa mapear o caminho no código (veja seção 4). O YAML é ideal para **ajustar regras existentes** (peso, severidade, enabled).

---

## 4. Abordagem 2 — Regra Simples em Código

**Quando usar:** Você precisa checar um campo que existe diretamente no YAML do recurso Kubernetes.

**Exemplo: Verificar se o Deployment tem o label `app.kubernetes.io/version`**

### Passo 1 — Adicionar a regra em `_get_default_rules()`

Arquivo: `src/application/services/scorecard_service.py`

```python
def _get_default_rules(self) -> List[ValidationRule]:
    return [
        # ... regras existentes ...

        # === PILAR: COMPLIANCE ===
        ValidationRule(
            id="COMP-001",
            pillar=ValidationPillar.COMPLIANCE,
            name="Label de Versão Obrigatório",
            description="Workload deve ter o label 'app.kubernetes.io/version' definido",
            rule_type=ValidationRuleType.BOOLEAN,
            source="K8s API",
            severity=ValidationSeverity.WARNING,
            weight=5.0,
            applies_to=["Deployment", "StatefulSet", "DaemonSet"],
            remediation="Adicione 'app.kubernetes.io/version: v1.0.0' nos labels do metadata",
            documentation_url="https://kubernetes.io/docs/concepts/overview/working-with-objects/common-labels/"
        ),
    ]
```

### Passo 2 — Mapear o caminho em `_extract_value_from_resource()`

No mesmo arquivo, adicione ao dicionário `rule_paths`:

```python
def _extract_value_from_resource(self, rule_id: str, resource: Dict[str, Any],
                            namespace: str, name: str) -> Optional[Any]:

    rule_paths = {
        # ... caminhos existentes ...

        # Label de versão — acessa metadata.labels do recurso
        "COMP-001": "metadata.labels.app.kubernetes.io/version",
    }
    # resto do método permanece igual
```

**Como o caminho funciona:**

O método navega o dicionário usando notação de pontos:
- `"spec.replicas"` → `resource["spec"]["replicas"]`
- `"spec.template.spec.containers[0].image"` → `resource["spec"]["template"]["spec"]["containers"][0]["image"]`
- `"metadata.labels.app.kubernetes.io/version"` → `resource["metadata"]["labels"]["app.kubernetes.io/version"]`

### Passo 3 — Verificar

```python
# Teste manual no Python REPL
from src.application.services.scorecard_service import ScorecardService

service = ScorecardService()
scorecard = service.evaluate_resource("default", "meu-deployment", "Deployment")

# Ver resultado da regra COMP-001
for pillar in scorecard.pillar_scores.values():
    for result in pillar.validation_results:
        if result.rule_id == "COMP-001":
            print(f"Passou: {result.passed}")
            print(f"Valor encontrado: {result.actual_value}")
            print(f"Mensagem: {result.message}")
```

---

## 5. Abordagem 3 — Regra com Função Personalizada

**Quando usar:** A validação exige lógica que não pode ser expressada apenas com um caminho dot-notation — por exemplo: calcular algo a partir de múltiplos campos, comparar valores entre si, ou acessar mais de um container.

### Exemplo Simples: Verificar se TODOS os containers têm liveness probe

O caminho `containers[0].livenessProbe` só verifica o primeiro container. Para verificar todos:

### Passo 1 — Adicionar a regra

```python
ValidationRule(
    id="RES-015",
    pillar=ValidationPillar.RESILIENCE,
    name="Todos os Containers com Liveness Probe",
    description="Todos os containers do pod devem ter livenessProbe configurada",
    rule_type=ValidationRuleType.BOOLEAN,
    source="K8s API",
    severity=ValidationSeverity.ERROR,
    weight=8.0,
    applies_to=["Deployment", "StatefulSet", "DaemonSet"],
    remediation="Configure livenessProbe em todos os containers do pod spec",
),
```

### Passo 2 — Criar o método validador

O nome do método **deve seguir o padrão**: `_validate_{rule_id_em_snake_case_minúsculo}`

```python
def _validate_res_015(
    self,
    rule: ValidationRule,
    resource: Dict[str, Any],
    namespace: str,
    name: str,
) -> ValidationResult:
    """Verifica se todos os containers têm livenessProbe configurada."""

    containers = (
        resource.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )

    if not containers:
        return ValidationResult(
            rule_id=rule.id,
            rule_name=rule.name,
            pillar=rule.pillar,
            passed=False,
            severity=rule.severity,
            weight=rule.weight,
            message=f"{rule.name}: ❌ Nenhum container encontrado",
            actual_value=None,
            remediation=rule.remediation,
        )

    containers_sem_probe = [
        c.get("name", f"container-{i}")
        for i, c in enumerate(containers)
        if not c.get("livenessProbe")
    ]

    passed = len(containers_sem_probe) == 0

    if passed:
        message = f"{rule.name}: ✅ Todos os {len(containers)} container(s) têm liveness probe"
    else:
        message = (
            f"{rule.name}: ❌ Containers sem liveness probe: "
            f"{', '.join(containers_sem_probe)}"
        )

    return ValidationResult(
        rule_id=rule.id,
        rule_name=rule.name,
        pillar=rule.pillar,
        passed=passed,
        severity=rule.severity,
        weight=rule.weight,
        message=message,
        actual_value=containers_sem_probe if not passed else [],
        expected_value=[],
        remediation=rule.remediation,
        documentation_url=rule.documentation_url,
    )
```

> **Convenção de nomes:** `RES-015` → `_validate_res_015`, `SEC-004` → `_validate_sec_004`, `PERF-001` → `_validate_perf_001`

---

### Exemplo Complexo: Verificar razão de eficiência memória/CPU entre todos containers

Valida que nenhum container tem limit de CPU mais de 4× o request, **considerando todos os containers**:

```python
ValidationRule(
    id="PERF-003",
    pillar=ValidationPillar.PERFORMANCE,
    name="Ratio CPU Adequado em Todos os Containers",
    description="Nenhum container deve ter limit de CPU > 4× o request",
    rule_type=ValidationRuleType.NUMERIC,
    source="Custom",
    severity=ValidationSeverity.WARNING,
    weight=6.0,
    max_value=4.0,
    applies_to=["Deployment"],
    remediation="Reduza a diferença entre CPU limit e request para no máximo 4×",
),
```

```python
def _validate_perf_003(
    self,
    rule: ValidationRule,
    resource: Dict[str, Any],
    namespace: str,
    name: str,
) -> ValidationResult:
    """Valida ratio CPU em todos os containers."""

    def parse_cpu_to_millicores(cpu_str: str) -> float:
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1])
        return float(cpu_str) * 1000

    containers = (
        resource.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )

    violations = []

    for c in containers:
        container_name = c.get("name", "unknown")
        resources = c.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})

        cpu_req = requests.get("cpu")
        cpu_lim = limits.get("cpu")

        if not cpu_req or not cpu_lim:
            continue  # sem dados, pula (outra regra cobre isso)

        req_m = parse_cpu_to_millicores(cpu_req)
        lim_m = parse_cpu_to_millicores(cpu_lim)

        if req_m > 0:
            ratio = lim_m / req_m
            if ratio > rule.max_value:
                violations.append(
                    f"{container_name}: ratio={ratio:.1f}× (max={rule.max_value}×)"
                )

    passed = len(violations) == 0

    return ValidationResult(
        rule_id=rule.id,
        rule_name=rule.name,
        pillar=rule.pillar,
        passed=passed,
        severity=rule.severity,
        weight=rule.weight,
        message=(
            f"{rule.name}: ✅ Todos os ratios de CPU adequados"
            if passed
            else f"{rule.name}: ❌ Containers com ratio excessivo: {'; '.join(violations)}"
        ),
        actual_value=violations,
        expected_value=f"ratio ≤ {rule.max_value}×",
        remediation=rule.remediation,
    )
```

---

## 6. Abordagem 4 — Regra com Integração Externa

**Quando usar:** A validação precisa de dados que não estão no recurso Kubernetes — por exemplo, checar no Datadog se o serviço tem alertas configurados, ou no Backstage se o serviço tem dono definido.

### Exemplo: Verificar se o serviço tem monitor no Datadog

### Passo 1 — Injetar o cliente externo no `ScorecardService`

Em `src/application/services/scorecard_service.py`:

```python
class ScorecardService:

    def __init__(self, config_path: Optional[str] = None, datadog_repo=None):
        # ... init existente ...
        self.datadog_repo = datadog_repo  # injeta dependência

        self.logger.info("ScorecardService inicializado", extra={...})
```

Em `src/bootstrap/dependencies.py`, passe o repositório ao construir o service:

```python
def get_scorecard_service() -> ScorecardService:
    from src.application.services.scorecard_service import ScorecardService
    from src.infrastructure.datadog.repository import DatadogRepository

    datadog_repo = DatadogRepository() if settings.datadog.enabled else None
    return ScorecardService(
        config_path=settings.scorecard.config_path,
        datadog_repo=datadog_repo,
    )
```

### Passo 2 — Adicionar a regra

```python
ValidationRule(
    id="OPS-001",
    pillar=ValidationPillar.OPERATIONAL,
    name="Monitor Datadog Configurado",
    description="Serviço deve ter pelo menos um monitor ativo no Datadog",
    rule_type=ValidationRuleType.BOOLEAN,
    source="Datadog API",
    severity=ValidationSeverity.ERROR,
    weight=10.0,
    applies_to=["Deployment"],
    remediation="Crie um monitor no Datadog para este serviço via SLOConfig CRD",
    documentation_url="https://docs.datadoghq.com/monitors/",
),
```

### Passo 3 — Implementar o validador com chamada externa

```python
def _validate_ops_001(
    self,
    rule: ValidationRule,
    resource: Dict[str, Any],
    namespace: str,
    name: str,
) -> ValidationResult:
    """Verifica se existe monitor no Datadog para este serviço."""

    # Graceful degradation: se Datadog não estiver disponível, pula
    if not self.datadog_repo:
        return ValidationResult(
            rule_id=rule.id,
            rule_name=rule.name,
            pillar=rule.pillar,
            passed=True,          # Não penaliza se integração não configurada
            severity=rule.severity,
            weight=0.0,           # Peso zero para não afetar score
            message=f"{rule.name}: ⚠️ Datadog não configurado — validação ignorada",
        )

    # Busca o nome do serviço nas anotações ou usa o nome do deployment
    labels = resource.get("metadata", {}).get("labels", {})
    service_name = (
        labels.get("tags.datadoghq.com/service")
        or labels.get("app")
        or name
    )

    try:
        monitors = self.datadog_repo.list_monitors_by_tag(
            tag=f"service:{service_name}"
        )
        passed = len(monitors) > 0

        return ValidationResult(
            rule_id=rule.id,
            rule_name=rule.name,
            pillar=rule.pillar,
            passed=passed,
            severity=rule.severity,
            weight=rule.weight,
            message=(
                f"{rule.name}: ✅ {len(monitors)} monitor(s) encontrado(s) para '{service_name}'"
                if passed
                else f"{rule.name}: ❌ Nenhum monitor encontrado para o serviço '{service_name}'"
            ),
            actual_value=len(monitors),
            expected_value=">= 1",
            remediation=rule.remediation,
            documentation_url=rule.documentation_url,
        )

    except Exception as e:
        self.logger.warning(
            "Falha ao consultar monitors no Datadog",
            extra={"rule_id": rule.id, "service": service_name, "error": str(e)},
        )
        # Graceful degradation em caso de erro na API
        return ValidationResult(
            rule_id=rule.id,
            rule_name=rule.name,
            pillar=rule.pillar,
            passed=True,
            severity=rule.severity,
            weight=0.0,
            message=f"{rule.name}: ⚠️ Falha ao consultar Datadog — validação ignorada",
        )
```

---

### Exemplo: Integração com API interna (HTTP)

Para integrar com qualquer API HTTP (Backstage, PagerDuty, Vault, etc.):

```python
import requests
from functools import lru_cache

class ScorecardService:

    def __init__(self, config_path=None, internal_api_url: str = None):
        # ...
        self._internal_api_url = internal_api_url
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {os.getenv('INTERNAL_API_TOKEN', '')}",
            "Content-Type": "application/json",
        })

    @lru_cache(maxsize=128)
    def _get_service_owner(self, service_name: str) -> Optional[str]:
        """Consulta owner do serviço na API interna. Cache de 128 entradas."""
        if not self._internal_api_url:
            return None
        try:
            resp = self._session.get(
                f"{self._internal_api_url}/services/{service_name}/owner",
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json().get("owner")
        except Exception as e:
            self.logger.warning(f"Falha ao buscar owner de {service_name}: {e}")
            return None

    def _validate_ops_002(self, rule, resource, namespace, name):
        """Verifica se o serviço tem owner registrado na API interna."""
        service_name = resource.get("metadata", {}).get("labels", {}).get("app", name)
        owner = self._get_service_owner(service_name)

        passed = owner is not None
        return ValidationResult(
            rule_id=rule.id,
            rule_name=rule.name,
            pillar=rule.pillar,
            passed=passed,
            severity=rule.severity,
            weight=rule.weight,
            message=(
                f"{rule.name}: ✅ Owner: {owner}"
                if passed
                else f"{rule.name}: ❌ Serviço '{service_name}' sem owner registrado"
            ),
            remediation=rule.remediation,
        )
```

---

## 7. Adicionando um Novo Pilar

Para adicionar um pilar completamente novo (ex: `DATA_GOVERNANCE`):

### Passo 1 — Adicionar o enum em `src/domain/models.py`

```python
class ValidationPillar(str, Enum):
    RESILIENCE   = "resilience"
    SECURITY     = "security"
    COST         = "cost"
    PERFORMANCE  = "performance"
    OPERATIONAL  = "operational"
    COMPLIANCE   = "compliance"
    # Novo pilar:
    DATA_GOVERNANCE = "data_governance"
```

### Passo 2 — Definir o peso do pilar em `_calculate_overall_score()`

Arquivo: `src/application/services/scorecard_service.py`

```python
def _calculate_overall_score(self, pillar_scores):
    pillar_weights = {
        ValidationPillar.RESILIENCE:     30.0,
        ValidationPillar.SECURITY:       25.0,
        ValidationPillar.COMPLIANCE:     20.0,
        ValidationPillar.PERFORMANCE:    15.0,
        ValidationPillar.OPERATIONAL:    10.0,
        ValidationPillar.COST:           10.0,
        # Novo pilar (ajuste os outros pesos para somar o que desejar):
        ValidationPillar.DATA_GOVERNANCE: 5.0,
    }
    # ...
```

> **Atenção:** Os pesos não precisam somar 100. O cálculo usa média ponderada, então o total é calculado automaticamente. Ao adicionar um novo pilar, os outros pilares ficam com peso relativamente menor — considere rebalancear.

### Passo 3 — Criar as regras do novo pilar

```python
ValidationRule(
    id="DG-001",
    pillar=ValidationPillar.DATA_GOVERNANCE,
    name="Classificação de Dados definida",
    description="Workload deve declarar o nível de classificação dos dados que processa",
    rule_type=ValidationRuleType.ENUM,
    source="K8s API",
    severity=ValidationSeverity.WARNING,
    weight=8.0,
    allowed_values=["public", "internal", "confidential", "restricted"],
    applies_to=["Deployment"],
    remediation="Adicione o label 'data-classification: internal' ao deployment",
),
```

```python
# Em _extract_value_from_resource():
"DG-001": "metadata.labels.data-classification",
```

---

## 8. Referência Rápida dos Tipos de Regra

### BOOLEAN — tudo que é "existe ou não existe"

```python
ValidationRule(
    id="XXX-000",
    rule_type=ValidationRuleType.BOOLEAN,
    # Não precisa de: expected_value, min_value, max_value, allowed_values, regex_pattern
    # Passa se: o valor extraído for diferente de None/False/[]
)
```

```python
# No _extract_value_from_resource:
"XXX-000": "spec.template.spec.containers[0].livenessProbe",
#            ^-- se retornar qualquer objeto não-None, PASSA
```

### NUMERIC — faixas numéricas

```python
ValidationRule(
    id="XXX-000",
    rule_type=ValidationRuleType.NUMERIC,
    min_value=2.0,    # opcional — valor mínimo
    max_value=10.0,   # opcional — valor máximo
    # Passa se: min_value <= valor_extraido <= max_value
)
```

```python
# Suporte automático a unidades Kubernetes:
# "100m"  → 0.1 (CPU cores)
# "500m"  → 0.5
# "512Mi" → 512 (MiB)
# "2Gi"   → 2048 (MiB)
# "3"     → 3.0 (puro)
```

### ENUM — valores permitidos

```python
ValidationRule(
    id="XXX-000",
    rule_type=ValidationRuleType.ENUM,
    allowed_values=["RollingUpdate", "Recreate"],
    # Passa se: valor_extraido in allowed_values
)
```

### REGEX — padrões de string

```python
ValidationRule(
    id="XXX-000",
    rule_type=ValidationRuleType.REGEX,
    regex_pattern=r"^v\d+\.\d+\.\d+$",  # semver: v1.2.3
    # Passa se: re.match(regex_pattern, valor_extraido)
)
```

**Padrões úteis:**

```python
# Não usar :latest
r"^(?!.*:latest$).+"

# Semver com prefixo v
r"^v\d+\.\d+\.\d+$"

# Email corporativo
r"^[^@]+@empresa\.com$"

# Não vazio
r"^.+$"
```

---

## 9. Como Escolher Peso e Severidade

### Peso (weight)

O peso determina **quanto essa regra influencia o score do pilar**.

| Faixa | Quando usar | Exemplos |
|---|---|---|
| 1–3 | Recomendação opcional | Graceful shutdown, labels informativos |
| 4–6 | Boa prática importante | CPU limits, rollout strategy |
| 7–9 | Requisito de segurança/resiliência | HPA, non-root, privilege escalation |
| 10+ | Crítico para o serviço funcionar | Liveness probe, CPU requests, tag de imagem |

### Severidade

| Severidade | Impacto no score | Conta como | Quando usar |
|---|---|---|---|
| `CRITICAL` | Bloqueia (peso máximo) | critical_issues | Vulnerabilidade crítica, configuração perigosa |
| `ERROR` | Alto impacto | error_issues | Falta de probe, imagem :latest, root container |
| `WARNING` | Médio impacto | warning_issues | Falta de HPA, ausência de limits, policy não aplicada |
| `INFO` | Baixo impacto | — | Recomendações, boas práticas |
| `OPTIONAL` | Sem impacto no score | — | Sugestões que não devem penalizar |

> **Dica:** `error_issues > 3` dispara notificação mesmo com score alto. Use `ERROR` com cuidado.

---

## 10. Escrevendo Testes

### Teste unitário para nova regra simples (BOOLEAN)

Arquivo: `tests/unit/test_domain_models.py` ou crie `tests/unit/test_nova_regra.py`

```python
import pytest
from unittest.mock import MagicMock, patch
from src.application.services.scorecard_service import ScorecardService
from src.domain.models import ValidationPillar, ValidationSeverity


@pytest.fixture
def service_sem_cluster():
    """ScorecardService com clientes K8s mockados."""
    with patch("src.application.services.scorecard_service.get_k8s_apis") as mock_apis:
        mock_core = MagicMock()
        mock_apps = MagicMock()
        mock_custom = MagicMock()
        mock_apis.return_value = (mock_core, mock_apps, mock_custom)

        with patch("src.application.services.scorecard_service.client"):
            service = ScorecardService()
    return service


def make_deployment(labels=None, containers=None, replicas=2):
    """Cria um dicionário simulando um Deployment K8s."""
    containers = containers or [
        {
            "name": "app",
            "image": "myrepo/app:v1.0.0",
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "500m", "memory": "512Mi"},
            },
            "livenessProbe": {"httpGet": {"path": "/health", "port": 8080}},
            "readinessProbe": {"httpGet": {"path": "/ready", "port": 8080}},
            "securityContext": {
                "runAsNonRoot": True,
                "allowPrivilegeEscalation": False,
            },
        }
    ]
    return {
        "metadata": {
            "name": "test-deployment",
            "namespace": "default",
            "labels": labels or {"app": "test-deployment"},
        },
        "spec": {
            "replicas": replicas,
            "strategy": {"type": "RollingUpdate"},
            "template": {
                "spec": {
                    "terminationGracePeriodSeconds": 30,
                    "securityContext": {"runAsNonRoot": True},
                    "containers": containers,
                }
            },
        },
    }


class TestNovaRegra:

    def test_comp_001_aprovado_quando_label_presente(self, service_sem_cluster):
        """COMP-001 deve aprovar quando o label 'team' está presente."""
        deployment = make_deployment(labels={"app": "test", "team": "sre"})
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "COMP-001")

        result = service_sem_cluster._validate_generic(rule, deployment, "default", "test")

        assert result.passed is True
        assert result.rule_id == "COMP-001"

    def test_comp_001_reprovado_quando_label_ausente(self, service_sem_cluster):
        """COMP-001 deve reprovar quando o label 'team' está ausente."""
        deployment = make_deployment(labels={"app": "test"})  # sem label 'team'
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "COMP-001")

        result = service_sem_cluster._validate_generic(rule, deployment, "default", "test")

        assert result.passed is False
        assert result.severity == ValidationSeverity.WARNING

    def test_comp_001_tem_peso_correto(self, service_sem_cluster):
        """COMP-001 deve ter o peso configurado corretamente."""
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "COMP-001")
        assert rule.weight == 5.0
        assert rule.pillar == ValidationPillar.COMPLIANCE
```

### Teste para validador com função personalizada

```python
class TestRES015TodosContainersComProbe:

    def test_aprovado_quando_todos_containers_tem_probe(self, service_sem_cluster):
        deployment = make_deployment(containers=[
            {
                "name": "app",
                "image": "repo/app:v1",
                "livenessProbe": {"httpGet": {"path": "/health", "port": 8080}},
            },
            {
                "name": "sidecar",
                "image": "repo/sidecar:v1",
                "livenessProbe": {"tcpSocket": {"port": 9090}},
            },
        ])

        rule = next(r for r in service_sem_cluster.config.rules if r.id == "RES-015")
        result = service_sem_cluster._validate_res_015(rule, deployment, "default", "test")

        assert result.passed is True

    def test_reprovado_quando_um_container_sem_probe(self, service_sem_cluster):
        deployment = make_deployment(containers=[
            {
                "name": "app",
                "image": "repo/app:v1",
                "livenessProbe": {"httpGet": {"path": "/health", "port": 8080}},
            },
            {
                "name": "sidecar",
                "image": "repo/sidecar:v1",
                # sem livenessProbe
            },
        ])

        rule = next(r for r in service_sem_cluster.config.rules if r.id == "RES-015")
        result = service_sem_cluster._validate_res_015(rule, deployment, "default", "test")

        assert result.passed is False
        assert "sidecar" in result.message

    def test_reprovado_quando_sem_containers(self, service_sem_cluster):
        deployment = make_deployment(containers=[])
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "RES-015")
        result = service_sem_cluster._validate_res_015(rule, deployment, "default", "test")

        assert result.passed is False
```

### Teste para validador com integração externa

```python
class TestOPS001MonitorDatadog:

    def test_aprovado_quando_monitor_existe(self, service_sem_cluster):
        # Simula Datadog retornando 2 monitors
        service_sem_cluster.datadog_repo = MagicMock()
        service_sem_cluster.datadog_repo.list_monitors_by_tag.return_value = [
            {"id": 1, "name": "monitor-1"},
            {"id": 2, "name": "monitor-2"},
        ]

        deployment = make_deployment(labels={"app": "payment-api"})
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "OPS-001")
        result = service_sem_cluster._validate_ops_001(rule, deployment, "default", "payment-api")

        assert result.passed is True
        assert "2 monitor(s)" in result.message

    def test_reprovado_quando_sem_monitor(self, service_sem_cluster):
        service_sem_cluster.datadog_repo = MagicMock()
        service_sem_cluster.datadog_repo.list_monitors_by_tag.return_value = []

        deployment = make_deployment(labels={"app": "payment-api"})
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "OPS-001")
        result = service_sem_cluster._validate_ops_001(rule, deployment, "default", "payment-api")

        assert result.passed is False
        assert "payment-api" in result.message

    def test_graceful_degradation_quando_datadog_indisponivel(self, service_sem_cluster):
        service_sem_cluster.datadog_repo = MagicMock()
        service_sem_cluster.datadog_repo.list_monitors_by_tag.side_effect = Exception("Connection refused")

        deployment = make_deployment()
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "OPS-001")
        result = service_sem_cluster._validate_ops_001(rule, deployment, "default", "test")

        # Deve aprovar com peso 0 — não penaliza por falha de infraestrutura
        assert result.passed is True
        assert result.weight == 0.0

    def test_graceful_degradation_quando_sem_datadog_configurado(self, service_sem_cluster):
        service_sem_cluster.datadog_repo = None  # não configurado

        deployment = make_deployment()
        rule = next(r for r in service_sem_cluster.config.rules if r.id == "OPS-001")
        result = service_sem_cluster._validate_ops_001(rule, deployment, "default", "test")

        assert result.passed is True
        assert result.weight == 0.0
```

### Rodando os testes

```bash
# Rodar todos os testes
poetry run pytest

# Rodar apenas os testes da nova regra
poetry run pytest tests/unit/test_nova_regra.py -v

# Rodar com cobertura e ver quais linhas não foram cobertas
poetry run pytest tests/unit/test_nova_regra.py --cov=src.application.services.scorecard_service --cov-report=term-missing
```

---

## 11. Troubleshooting

### Regra não aparece no scorecard

**Verifique:**

1. `enabled=True` na definição da regra
2. O `kind` do recurso está em `rule.applies_to`
3. A regra foi adicionada ao retorno de `_get_default_rules()`

```bash
# Ver regras carregadas nos logs do operator
kubectl logs -n titlis-system -l app=titlis-operator | grep "ScorecardService inicializado"
# Deve mostrar: {"rules_count": 27, "enabled_rules": 27, ...}
```

---

### Regra sempre reprovada (mesmo com configuração correta)

**Causas comuns:**

1. **Caminho K8s incorreto** — use `kubectl get deployment <nome> -o json` para ver o caminho exato

```bash
# Inspecionar o recurso real
kubectl get deployment meu-app -n production -o json | jq '.spec.template.spec.containers[0]'
```

2. **Tipo de dados incompatível** — o campo pode ser `bool` no K8s mas você está comparando como string

```python
# Problema: campo é False (bool), mas BOOLEAN espera None para "não configurado"
# Solução: use ENUM ou crie validador personalizado para booleanos False

def _validate_sec_003(self, rule, resource, namespace, name):
    """allowPrivilegeEscalation deve ser explicitamente False."""
    value = (
        resource.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [{}])[0]
        .get("securityContext", {})
        .get("allowPrivilegeEscalation")
    )
    # CORRETO: verifica se é False explicitamente
    passed = value is False
    ...
```

3. **Função callable no `rule_paths` chamada antes do recurso ser carregado** — se você usar `self._check_hpa_exists(namespace, name)` diretamente no dicionário (sem lambda), ela é chamada na construção do dicionário, antes de ter o recurso

```python
# ERRADO — chama a função na hora de montar o dict
rule_paths = {
    "RES-007": self._check_hpa_exists(namespace, name),  # chamado imediatamente
}

# CORRETO — usa lambda para chamar apenas quando necessário
rule_paths = {
    "RES-007": lambda: self._check_hpa_exists(namespace, name),
}
```

---

### Impacto no score diferente do esperado

**Verifique o peso relativo:**

```python
# Ver peso total do pilar RESILIENCE
service = ScorecardService()
resilience_rules = [r for r in service.config.rules
                    if r.pillar.value == "resilience" and r.enabled]

total_weight = sum(r.weight for r in resilience_rules)
print(f"Peso total RESILIENCE: {total_weight}")

for r in resilience_rules:
    pct = r.weight / total_weight * 100
    print(f"  {r.id}: {r.weight} ({pct:.1f}% do pilar)")
```

---

### Notificação Slack não é enviada

**Verifique a cadeia de decisão:**

1. `SLACK_ENABLED=true`?
2. `SLACK_BOT_TOKEN` ou `SLACK_WEBHOOK_URL` configurados?
3. O score está abaixo dos thresholds configurados?
4. O cooldown (padrão 60 min) ainda está ativo?

```bash
# Ver última vez que uma notificação foi enviada
kubectl logs -n titlis-system -l app=titlis-operator | grep "Namespace digest enviado"
```

---

### Checklist Final ao Adicionar uma Nova Regra

```
[ ] ID único no formato PILAR-NNN (ex: COMP-001, OPS-002)
[ ] Regra adicionada em _get_default_rules()
[ ] Caminho mapeado em _extract_value_from_resource() OU método _validate_xxx() criado
[ ] Pilar correto e peso condizente com a importância
[ ] Severidade adequada (ERROR para requisitos, WARNING para boas práticas)
[ ] applies_to correto (["Deployment"] ou incluir StatefulSet/DaemonSet)
[ ] remediation descritiva e acionável
[ ] Teste unitário cobrindo aprovado, reprovado e edge cases
[ ] Se integração externa: graceful degradation implementado (peso 0 em falha)
[ ] Se novo pilar: peso adicionado em _calculate_overall_score()
[ ] Validado localmente: poetry run pytest tests/ -v
```
