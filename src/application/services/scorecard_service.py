import re
import yaml
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from kubernetes import client
from src.domain.models import (
    ResourceScorecard,
    PillarScore,
    ValidationResult,
    ValidationRule,
    ValidationPillar,
    ValidationSeverity,
    ValidationRuleType,
    ScorecardConfig,
    CriticalityLevel,
)
from src.infrastructure.kubernetes.client import get_k8s_apis
from src.utils.json_logger import get_logger
from src.infrastructure.kubernetes.state_store import KubeStateStore

logger = get_logger(__name__)


class ScorecardService:
    def __init__(self, config_path: Optional[str] = None):
        self.logger = get_logger(self.__class__.__name__)
        self.config = self._load_config(config_path)
        self.core, self.apps, self.custom = get_k8s_apis()
        self.autoscaling_v2 = client.AutoscalingV2Api()
        self.networking_v1 = client.NetworkingV1Api()
        self.state_store = KubeStateStore(
            namespace="titlis-system", name="scorecard-state"
        )
        self._validation_cache: Dict[str, ResourceScorecard] = {}
        self._cache_ttl = timedelta(minutes=5)
        self.logger.info(
            "ScorecardService inicializado",
            extra={
                "rules_count": len(self.config.rules),
                "enabled_rules": len([r for r in self.config.rules if r.enabled]),
                "pilars": list(set(r.pillar for r in self.config.rules if r.enabled)),
            },
        )

    def _load_config(self, config_path: Optional[str]) -> ScorecardConfig:
        default_rules = self._get_default_rules()

        if config_path:
            try:
                with open(config_path, "r") as f:
                    config_data = yaml.safe_load(f)
                    return self._parse_config(config_data, default_rules)
            except Exception:
                self.logger.exception("Erro ao carregar configuração")

        return ScorecardConfig(rules=default_rules)

    def _get_default_rules(self) -> List[ValidationRule]:
        return [
            ValidationRule(
                id="RES-001",
                pillar=ValidationPillar.RESILIENCE,
                name="Liveness Probe Configurada",
                description="Container deve ter livenessProbe configurada",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=10.0,
                remediation="Adicione livenessProbe para detectar containers travados",
                documentation_url="https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/",
            ),
            ValidationRule(
                id="RES-002",
                pillar=ValidationPillar.RESILIENCE,
                name="Readiness Probe Configurada",
                description="Container deve ter readinessProbe configurada",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=10.0,
                remediation="Adicione readinessProbe para controle de tráfego",
                documentation_url="https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/",
            ),
            ValidationRule(
                id="RES-003",
                pillar=ValidationPillar.RESILIENCE,
                name="CPU Requests Definidos",
                description="Container deve ter requests de CPU definidos",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=8.0,
                remediation="Defina requests.cpu para planejamento de recursos",
            ),
            ValidationRule(
                id="RES-004",
                pillar=ValidationPillar.RESILIENCE,
                name="CPU Limits Definidos",
                description="Container deve ter limits de CPU definidos",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=5.0,
                remediation="Defina limits.cpu para evitar consumo excessivo",
            ),
            ValidationRule(
                id="RES-005",
                pillar=ValidationPillar.RESILIENCE,
                name="Memory Requests Definidos",
                description="Container deve ter requests de memória definidos",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=8.0,
                remediation="Defina requests.memory para planejamento de recursos",
            ),
            ValidationRule(
                id="RES-006",
                pillar=ValidationPillar.RESILIENCE,
                name="Memory Limits Definidos",
                description="Container deve ter limits de memória definidos",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=5.0,
                remediation="Defina limits.memory para evitar OOM kills",
            ),
            ValidationRule(
                id="RES-007",
                pillar=ValidationPillar.RESILIENCE,
                name="HPA Configurado",
                description="Deployment deve ter HorizontalPodAutoscaler configurado",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=7.0,
                applies_to=["Deployment"],
                remediation="Configure HPA para auto-scaling baseado em demanda",
            ),
            ValidationRule(
                id="RES-008",
                pillar=ValidationPillar.RESILIENCE,
                name="HPA com Métricas",
                description="HPA deve estar configurado com métricas (CPU/memória)",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=5.0,
                applies_to=["Deployment"],
                remediation="Adicione métricas de CPU ou memória ao HPA",
            ),
            ValidationRule(
                id="RES-009",
                pillar=ValidationPillar.RESILIENCE,
                name="Graceful Shutdown Configurado",
                description="Pod deve ter terminationGracePeriodSeconds configurado",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.INFO,
                weight=3.0,
                remediation="Configure terminationGracePeriodSeconds para shutdown gracioso",
            ),
            ValidationRule(
                id="RES-010",
                pillar=ValidationPillar.RESILIENCE,
                name="Container Non-Root",
                description="Container não deve rodar como root",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=10.0,
                remediation="Configure securityContext.runAsNonRoot: true",
            ),
            ValidationRule(
                id="RES-011",
                pillar=ValidationPillar.RESILIENCE,
                name="Pod Security Context",
                description="Pod deve ter securityContext configurado",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=5.0,
                remediation="Configure securityContext no nível do pod",
            ),
            ValidationRule(
                id="RES-012",
                pillar=ValidationPillar.RESILIENCE,
                name="NetworkPolicy Aplicada",
                description="Deployment deve ter NetworkPolicy aplicada",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=7.0,
                applies_to=["Deployment", "StatefulSet"],
                remediation="Crie NetworkPolicy para limitar tráfego de rede",
            ),
            ValidationRule(
                id="RES-013",
                pillar=ValidationPillar.RESILIENCE,
                name="Replicas Mínimas",
                description="Deployment deve ter pelo menos 2 réplicas",
                rule_type=ValidationRuleType.NUMERIC,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=6.0,
                min_value=2,
                applies_to=["Deployment"],
                remediation="Aumente replicas para pelo menos 2 para alta disponibilidade",
            ),
            ValidationRule(
                id="RES-014",
                pillar=ValidationPillar.RESILIENCE,
                name="Estratégia de Rollout",
                description="Deployment deve ter estratégia de rollout configurada",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=4.0,
                applies_to=["Deployment"],
                remediation="Configure strategy.type: RollingUpdate com maxUnavailable/maxSurge",
            ),
            ValidationRule(
                id="SEC-001",
                pillar=ValidationPillar.SECURITY,
                name="Imagem com Tag Específica",
                description="Container deve usar tag específica, não 'latest'",
                rule_type=ValidationRuleType.REGEX,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=9.0,
                regex_pattern=r"^(?!.*:latest$).+$",
                remediation="Use tags versionadas (ex: v1.2.3) ao invés de 'latest'",
            ),
            ValidationRule(
                id="SEC-002",
                pillar=ValidationPillar.SECURITY,
                name="ReadOnly Root Filesystem",
                description="Container deve ter root filesystem como read-only",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=6.0,
                remediation="Configure securityContext.readOnlyRootFilesystem: true",
            ),
            ValidationRule(
                id="SEC-003",
                pillar=ValidationPillar.SECURITY,
                name="Privilege Escalation Desabilitado",
                description="Container não deve permitir escalação de privilégios",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.ERROR,
                weight=8.0,
                remediation="Configure securityContext.allowPrivilegeEscalation: false",
            ),
            ValidationRule(
                id="SEC-004",
                pillar=ValidationPillar.SECURITY,
                name="Capabilities Reduzidas",
                description="Container deve ter capabilities reduzidas",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=5.0,
                remediation="Remova capabilities desnecessárias: securityContext.capabilities.drop: ['ALL']",
            ),
            ValidationRule(
                id="PERF-001",
                pillar=ValidationPillar.PERFORMANCE,
                name="Resource Limits Adequados",
                description="Limits devem ser no máximo 3x maiores que requests",
                rule_type=ValidationRuleType.NUMERIC,
                source="Custom",
                severity=ValidationSeverity.WARNING,
                weight=4.0,
                max_value=3.0,
                remediation="Ajuste limites para serem mais próximos dos requests",
            ),
            ValidationRule(
                id="PERF-002",
                pillar=ValidationPillar.PERFORMANCE,
                name="HPA com Target Adequado",
                description="HPA target deve estar entre 50-90%",
                rule_type=ValidationRuleType.NUMERIC,
                source="K8s API",
                severity=ValidationSeverity.INFO,
                weight=3.0,
                min_value=50.0,
                max_value=90.0,
                remediation="Ajuste target para faixa ideal (50-90%)",
            ),
            # ── Regras avançadas de HPA (Perfil Leve) ────────────────────────────
            ValidationRule(
                id="RES-016",
                pillar=ValidationPillar.RESILIENCE,
                name="HPA MinReplicas >= 2",
                description="HPA deve ter minReplicas >= 2 para evitar cold start",
                rule_type=ValidationRuleType.NUMERIC,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=5.0,
                min_value=2.0,
                applies_to=["Deployment"],
                remediation="Configure HPA minReplicas >= 2",
            ),
            ValidationRule(
                id="PERF-003",
                pillar=ValidationPillar.PERFORMANCE,
                name="HPA CPU Target <= 70%",
                description="HPA CPU utilization target deve ser <= 70% para escalar antes de saturar",
                rule_type=ValidationRuleType.NUMERIC,
                source="K8s API",
                severity=ValidationSeverity.INFO,
                weight=3.0,
                max_value=70.0,
                applies_to=["Deployment"],
                remediation="Reduza o target de CPU do HPA para <= 70%",
            ),
            # ── Regras avançadas de HPA (Perfil Rígido — apenas apps críticas) ──
            ValidationRule(
                id="RES-017",
                pillar=ValidationPillar.RESILIENCE,
                name="HPA ScaleUp Stabilization == 0s",
                description="HPA behavior.scaleUp.stabilizationWindowSeconds deve ser 0 para resposta imediata",
                rule_type=ValidationRuleType.NUMERIC,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=4.0,
                max_value=0.0,
                applies_to=["Deployment"],
                remediation="Configure behavior.scaleUp.stabilizationWindowSeconds: 0",
                criticality_profile="rigid",
            ),
            ValidationRule(
                id="RES-018",
                pillar=ValidationPillar.RESILIENCE,
                name="HPA ScaleDown Stabilization >= 300s",
                description="HPA behavior.scaleDown.stabilizationWindowSeconds deve ser >= 300 para evitar flapping",
                rule_type=ValidationRuleType.NUMERIC,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=4.0,
                min_value=300.0,
                applies_to=["Deployment"],
                remediation="Configure behavior.scaleDown.stabilizationWindowSeconds: 300",
                criticality_profile="rigid",
            ),
            ValidationRule(
                id="RES-019",
                pillar=ValidationPillar.RESILIENCE,
                name="HPA com Políticas Explícitas",
                description="HPA deve ter políticas de scaleUp e scaleDown explícitas",
                rule_type=ValidationRuleType.BOOLEAN,
                source="K8s API",
                severity=ValidationSeverity.WARNING,
                weight=4.0,
                applies_to=["Deployment"],
                remediation="Configure behavior.scaleUp.policies e behavior.scaleDown.policies",
                criticality_profile="rigid",
            ),
        ]

    def _parse_config(
        self, config_data: Dict[str, Any], default_rules: List[ValidationRule]
    ) -> ScorecardConfig:
        rules = []
        rule_map = {r.id: r for r in default_rules}

        if "rules" in config_data:
            for rule_data in config_data["rules"]:
                rule_id = rule_data.get("id")
                if rule_id in rule_map:
                    rule = rule_map[rule_id]
                    if "enabled" in rule_data:
                        rule.enabled = rule_data["enabled"]
                    if "weight" in rule_data:
                        rule.weight = rule_data["weight"]
                    if "severity" in rule_data:
                        rule.severity = ValidationSeverity(rule_data["severity"])
                else:
                    rule = ValidationRule(
                        id=rule_id,
                        pillar=ValidationPillar(rule_data["pillar"]),
                        name=rule_data["name"],
                        description=rule_data.get("description", ""),
                        rule_type=ValidationRuleType(rule_data["type"]),
                        source=rule_data.get("source", "Custom"),
                        severity=ValidationSeverity(
                            rule_data.get("severity", "warning")
                        ),
                        weight=rule_data.get("weight", 1.0),
                        enabled=rule_data.get("enabled", True),
                        applies_to=rule_data.get("applies_to", ["Deployment"]),
                        expected_value=rule_data.get("expected_value"),
                        min_value=rule_data.get("min_value"),
                        max_value=rule_data.get("max_value"),
                        allowed_values=rule_data.get("allowed_values"),
                        regex_pattern=rule_data.get("regex_pattern"),
                        remediation=rule_data.get("remediation"),
                        documentation_url=rule_data.get("documentation_url"),
                    )
                    rule_map[rule_id] = rule

        rules = list(rule_map.values())

        config = ScorecardConfig(rules=rules)

        if "notification_thresholds" in config_data:
            thresholds = config_data["notification_thresholds"]
            config.notify_critical_threshold = thresholds.get("critical", 70.0)
            config.notify_error_threshold = thresholds.get("error", 80.0)
            config.notify_warning_threshold = thresholds.get("warning", 90.0)

        if "notification_settings" in config_data:
            settings = config_data["notification_settings"]
            config.notification_cooldown_minutes = settings.get("cooldown_minutes", 60)
            config.batch_notifications = settings.get("batch", True)
            config.batch_interval_minutes = settings.get("batch_interval", 15)

        if "excluded_namespaces" in config_data:
            config.excluded_namespaces.extend(config_data["excluded_namespaces"])

        return config

    def evaluate_resource(
        self, namespace: str, name: str, kind: str = "Deployment"
    ) -> ResourceScorecard:
        cache_key = f"{namespace}/{name}/{kind}"
        if cache_key in self._validation_cache:
            cached = self._validation_cache[cache_key]
            if datetime.now(timezone.utc) - cached.timestamp < self._cache_ttl:
                self.logger.debug(f"Usando cache para {cache_key}")
                return cached

        resource = self._get_resource(namespace, name, kind)
        if not resource:
            raise ValueError(f"Recurso {namespace}/{name}/{kind} não encontrado")

        criticality_level = self._detect_criticality(resource)

        applicable_rules = [
            r
            for r in self.config.rules
            if r.enabled
            and kind in r.applies_to
            and (
                r.criticality_profile is None
                or r.criticality_profile == criticality_level.value
            )
        ]

        validation_results = []
        for rule in applicable_rules:
            result = self._validate_rule(rule, resource, namespace, name)
            validation_results.append(result)

        pillar_scores = self._calculate_pillar_scores(validation_results)
        overall_score = self._calculate_overall_score(pillar_scores)

        critical_issues = sum(
            1
            for r in validation_results
            if not r.passed and r.severity == ValidationSeverity.CRITICAL
        )
        error_issues = sum(
            1
            for r in validation_results
            if not r.passed and r.severity == ValidationSeverity.ERROR
        )
        warning_issues = sum(
            1
            for r in validation_results
            if not r.passed and r.severity == ValidationSeverity.WARNING
        )
        passed_checks = sum(1 for r in validation_results if r.passed)
        total_checks = len(validation_results)

        scorecard = ResourceScorecard(
            resource_name=name,
            resource_namespace=namespace,
            resource_kind=kind,
            pillar_scores=pillar_scores,
            overall_score=overall_score,
            critical_issues=critical_issues,
            error_issues=error_issues,
            warning_issues=warning_issues,
            passed_checks=passed_checks,
            total_checks=total_checks,
            criticality_level=criticality_level.value,
        )

        self._validation_cache[cache_key] = scorecard

        if self.config.store_history:
            self._store_history(scorecard)

        return scorecard

    def _get_resource(
        self, namespace: str, name: str, kind: str
    ) -> Optional[Dict[str, Any]]:
        try:
            result_dict: Optional[Dict[str, Any]] = None
            if kind == "Deployment":
                resource = self.apps.read_namespaced_deployment(name, namespace)
                result_dict = dict(resource.to_dict())
            elif kind == "StatefulSet":
                resource = self.apps.read_namespaced_stateful_set(name, namespace)
                result_dict = dict(resource.to_dict())
            elif kind == "DaemonSet":
                resource = self.apps.read_namespaced_daemon_set(name, namespace)
                result_dict = dict(resource.to_dict())
            elif kind == "HorizontalPodAutoscaler":
                resource = (
                    self.autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                        name, namespace
                    )
                )
                result_dict = dict(resource.to_dict())
            else:
                self.logger.warning(f"Tipo de recurso não suportado: {kind}")
                return None
            return result_dict
        except Exception:
            self.logger.exception(f"Erro ao buscar recurso {namespace}/{name}/{kind}: ")
            return None

    def _validate_rule(
        self, rule: ValidationRule, resource: Dict[str, Any], namespace: str, name: str
    ) -> ValidationResult:
        validator_name = f"_validate_{rule.id.replace('-', '_').lower()}"
        validator = getattr(self, validator_name, None)

        if validator:
            result: ValidationResult = validator(rule, resource, namespace, name)
            return result
        else:
            return self._validate_generic(rule, resource, namespace, name)

    def _validate_generic(
        self, rule: ValidationRule, resource: Dict[str, Any], namespace: str, name: str
    ) -> ValidationResult:
        value = self._extract_value_from_resource(rule.id, resource, namespace, name)

        passed = False
        message = ""

        if rule.rule_type == ValidationRuleType.BOOLEAN:
            passed = value is not None
            message = (
                f"{rule.name}: {'✅ Configurado' if passed else '❌ Não configurado'}"
            )

        elif rule.rule_type == ValidationRuleType.NUMERIC and value is not None:
            try:
                if isinstance(value, str):
                    if value.endswith("m"):
                        num_value = float(value[:-1]) / 1000
                    elif value.endswith("Mi"):
                        num_value = float(value[:-2])
                    elif value.endswith("Gi"):
                        num_value = float(value[:-2]) * 1024
                    else:
                        num_value = float(value)
                else:
                    num_value = float(value)

                passed = True

                if rule.min_value is not None and num_value < rule.min_value:
                    passed = False
                    message = f"{rule.name}: ❌ Valor {num_value} abaixo do mínimo {rule.min_value}"
                elif rule.max_value is not None and num_value > rule.max_value:
                    passed = False
                    message = f"{rule.name}: ❌ Valor {num_value} acima do máximo {rule.max_value}"
                else:
                    message = (
                        f"{rule.name}: ✅ Valor {num_value} dentro da faixa esperada"
                    )

            except (ValueError, TypeError) as e:
                passed = False
                message = f"{rule.name}: ❌ Valor inválido: {value} ({str(e)})"

        elif rule.rule_type == ValidationRuleType.ENUM and value is not None:
            if rule.allowed_values:
                passed = value in rule.allowed_values
                message = f"{rule.name}: {'✅ Valor permitido' if passed else f'❌ Valor {value} não permitido'}"
            else:
                passed = False
                message = f"{rule.name}: ❌ Lista de valores permitidos não definida"

        elif rule.rule_type == ValidationRuleType.REGEX and value is not None:
            if rule.regex_pattern:
                try:
                    passed = bool(re.match(rule.regex_pattern, str(value)))
                    message = f"{rule.name}: {'✅ Valor válido' if passed else f'❌ Valor {value} não corresponde ao padrão'}"
                except re.error as e:
                    passed = False
                    message = f"{rule.name}: ❌ Padrão regex inválido: {str(e)}"
            else:
                passed = False
                message = f"{rule.name}: ❌ Padrão regex não definido"

        else:
            passed = False
            if value is None:
                message = f"{rule.name}: ❌ Configuração não encontrada"
            else:
                message = f"{rule.name}: ❌ Não aplicável ou valor não suportado: {type(value).__name__}"

        return ValidationResult(
            rule_id=rule.id,
            rule_name=rule.name,
            pillar=rule.pillar,
            passed=passed,
            severity=rule.severity,
            weight=rule.weight,
            message=message,
            actual_value=value,
            expected_value=rule.expected_value,
            remediation=rule.remediation,
            documentation_url=rule.documentation_url,
        )

    def _extract_value_from_resource(
        self, rule_id: str, resource: Dict[str, Any], namespace: str, name: str
    ) -> Optional[Any]:
        rule_paths = {
            "RES-001": "spec.template.spec.containers[0].livenessProbe",
            "RES-002": "spec.template.spec.containers[0].readinessProbe",
            "RES-003": "spec.template.spec.containers[0].resources.requests.cpu",
            "RES-004": "spec.template.spec.containers[0].resources.limits.cpu",
            "RES-005": "spec.template.spec.containers[0].resources.requests.memory",
            "RES-006": "spec.template.spec.containers[0].resources.limits.memory",
            "RES-007": self._check_hpa_exists(namespace, name),
            "RES-008": self._check_hpa_metrics(namespace, name),
            "RES-009": "spec.template.spec.terminationGracePeriodSeconds",
            "RES-010": "spec.template.spec.containers[0].securityContext.runAsNonRoot",
            "RES-011": "spec.template.spec.securityContext",
            "RES-012": self._check_network_policy_exists(namespace, name),
            "RES-013": "spec.replicas",
            "RES-014": "spec.strategy",
            "SEC-001": "spec.template.spec.containers[0].image",
            "SEC-002": "spec.template.spec.containers[0].securityContext.readOnlyRootFilesystem",
            "SEC-003": "spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation",
            "SEC-004": "spec.template.spec.containers[0].securityContext.capabilities.drop",
            "PERF-001": self._calculate_limit_ratio(resource),
            "PERF-002": self._get_hpa_target(namespace, name),
            "RES-016": self._get_hpa_min_replicas(namespace, name),
            "PERF-003": self._get_hpa_target(namespace, name),
            "RES-017": self._get_hpa_scale_up_stabilization(namespace, name),
            "RES-018": self._get_hpa_scale_down_stabilization(namespace, name),
            "RES-019": self._check_hpa_behavior_policies(namespace, name),
        }

        path_or_func = rule_paths.get(rule_id)

        if callable(path_or_func):
            try:
                return path_or_func()
            except Exception as e:
                self.logger.warning(
                    f"Erro ao executar função para regra {rule_id}",
                    extra={
                        "rule_id": rule_id,
                        "namespace": namespace,
                        "name": name,
                        "exception": str(e),
                    },
                )
                return None

        if isinstance(path_or_func, str):
            parts = path_or_func.split(".")
            current_value: Optional[Any] = resource

            for part in parts:
                if current_value is None:
                    return None

                if "[" in part and "]" in part:
                    try:
                        key_part = part.split("[")[0]
                        index_str = part.split("[")[1].rstrip("]")

                        if (
                            not isinstance(current_value, dict)
                            or key_part not in current_value
                        ):
                            return None

                        array_value = current_value[key_part]

                        if not isinstance(array_value, list):
                            return None

                        try:
                            index = int(index_str)
                        except ValueError:
                            return None

                        if index < 0 or index >= len(array_value):
                            return None

                        current_value = array_value[index]

                    except (KeyError, IndexError, TypeError, ValueError) as e:
                        self.logger.debug(
                            f"Erro ao acessar array {part}",
                            extra={
                                "rule_id": rule_id,
                                "part": part,
                                "current_value_type": type(current_value).__name__
                                if current_value
                                else "None",
                                "exception": str(e),
                            },
                        )
                        return None

                elif isinstance(current_value, dict) and part in current_value:
                    current_value = current_value[part]
                else:
                    return None

            return current_value

        return None

    def _check_hpa_exists(self, namespace: str, deployment_name: str) -> bool:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    return True
            return False
        except Exception:
            return False

    def _check_hpa_metrics(self, namespace: str, deployment_name: str) -> bool:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    return bool(hpa.spec.metrics)
            return False
        except Exception:
            return False

    def _detect_criticality(self, resource: Dict[str, Any]) -> CriticalityLevel:
        annotations = (resource.get("metadata") or {}).get("annotations") or {}
        if annotations.get("titlis.io/criticality") == "high":
            return CriticalityLevel.HIGH
        return CriticalityLevel.STANDARD

    def _get_hpa_min_replicas(
        self, namespace: str, deployment_name: str
    ) -> Optional[int]:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    val = hpa.spec.min_replicas
                    return int(val) if val is not None else None
            return None
        except Exception:
            return None

    def _get_hpa_scale_up_stabilization(
        self, namespace: str, deployment_name: str
    ) -> Optional[int]:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    behavior = hpa.spec.behavior
                    if behavior and behavior.scale_up:
                        val = behavior.scale_up.stabilization_window_seconds
                        return int(val) if val is not None else None
            return None
        except Exception:
            return None

    def _get_hpa_scale_down_stabilization(
        self, namespace: str, deployment_name: str
    ) -> Optional[int]:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    behavior = hpa.spec.behavior
                    if behavior and behavior.scale_down:
                        val = behavior.scale_down.stabilization_window_seconds
                        return int(val) if val is not None else None
            return None
        except Exception:
            return None

    def _check_hpa_behavior_policies(
        self, namespace: str, deployment_name: str
    ) -> Optional[bool]:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    behavior = hpa.spec.behavior
                    if behavior:
                        has_up = bool(behavior.scale_up and behavior.scale_up.policies)
                        has_down = bool(
                            behavior.scale_down and behavior.scale_down.policies
                        )
                        return has_up and has_down
            return None
        except Exception:
            return None

    def _check_network_policy_exists(self, namespace: str, resource_name: str) -> bool:
        try:
            policies = self.networking_v1.list_namespaced_network_policy(
                namespace
            ).items
            return len(policies) > 0
        except Exception:
            return False

    def _calculate_limit_ratio(self, resource: Dict[str, Any]) -> Optional[float]:
        try:
            containers = (
                resource.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
            )
            if not containers:
                return None

            container = containers[0]
            resources = container.get("resources", {})
            requests = resources.get("requests", {})
            limits = resources.get("limits", {})

            if "cpu" in requests and "cpu" in limits:

                def parse_cpu(cpu_str: str) -> float:
                    if cpu_str.endswith("m"):
                        return float(cpu_str[:-1])
                    else:
                        return float(cpu_str) * 1000

                req_cpu = parse_cpu(requests["cpu"])
                lim_cpu = parse_cpu(limits["cpu"])

                if req_cpu > 0:
                    return float(lim_cpu / req_cpu)

            return None
        except Exception:
            return None

    def _get_hpa_target(self, namespace: str, deployment_name: str) -> Optional[float]:
        try:
            hpas = self.autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
                namespace
            ).items
            for hpa in hpas:
                if (
                    hpa.spec.scale_target_ref.name == deployment_name
                    and hpa.spec.scale_target_ref.kind == "Deployment"
                ):
                    metrics = hpa.spec.metrics or []
                    for metric in metrics:
                        if metric.type == "Resource" and metric.resource.name == "cpu":
                            val = metric.resource.target.average_utilization
                            return float(val) if val is not None else None
            return None
        except Exception:
            return None

    def _calculate_pillar_scores(
        self, validation_results: List[ValidationResult]
    ) -> Dict[ValidationPillar, PillarScore]:
        pillar_results = defaultdict(list)
        for result in validation_results:
            pillar_results[result.pillar].append(result)

        pillar_scores = {}
        for pillar, results in pillar_results.items():
            total_weight = sum(r.weight for r in results)
            passed_weight = sum(r.weight for r in results if r.passed)

            score = (passed_weight / total_weight * 100) if total_weight > 0 else 100.0
            weighted_score = passed_weight

            pillar_scores[pillar] = PillarScore(
                pillar=pillar,
                score=score,
                max_score=100.0,
                passed_checks=sum(1 for r in results if r.passed),
                total_checks=len(results),
                weighted_score=weighted_score,
                validation_results=results,
            )

        return pillar_scores

    def _calculate_overall_score(
        self, pillar_scores: Dict[ValidationPillar, PillarScore]
    ) -> float:
        if not pillar_scores:
            return 100.0

        pillar_weights = {
            ValidationPillar.RESILIENCE: 30.0,
            ValidationPillar.SECURITY: 25.0,
            ValidationPillar.COMPLIANCE: 20.0,
            ValidationPillar.PERFORMANCE: 15.0,
            ValidationPillar.OPERATIONAL: 10.0,
            ValidationPillar.COST: 10.0,
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for pillar, score in pillar_scores.items():
            weight = pillar_weights.get(pillar, 10.0)
            total_weight += weight
            weighted_sum += score.score * weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _store_history(self, scorecard: ResourceScorecard) -> None:
        try:
            history_key = (
                f"history:{scorecard.resource_namespace}:{scorecard.resource_name}"
            )
            history_str = self.state_store.get(history_key) or "[]"
            history = eval(history_str)

            history.append(scorecard.to_dict())

            if len(history) > self.config.max_history_per_resource:
                history = history[-self.config.max_history_per_resource :]

            self.state_store.set(history_key, str(history))

        except Exception:
            self.logger.exception("Erro ao armazenar histórico")

    def get_validation_summary(self, namespace: str) -> Dict[str, Any]:
        deployments = self.apps.list_namespaced_deployment(namespace).items

        pillar_scores_dict: Dict[str, List[float]] = defaultdict(list)
        resources_list: List[Dict[str, Any]] = []
        summary: Dict[str, Any] = {
            "namespace": namespace,
            "total_resources": len(deployments),
            "pillar_scores": pillar_scores_dict,
            "resources": resources_list,
        }

        for deployment in deployments:
            name = deployment.metadata.name

            try:
                scorecard = self.evaluate_resource(namespace, name, "Deployment")

                summary["resources"].append(
                    {
                        "name": name,
                        "overall_score": scorecard.overall_score,
                        "critical_issues": scorecard.critical_issues,
                        "error_issues": scorecard.error_issues,
                        "warning_issues": scorecard.warning_issues,
                    }
                )

                for pillar, pillar_score in scorecard.pillar_scores.items():
                    summary["pillar_scores"][pillar.value].append(pillar_score.score)

            except Exception:
                self.logger.exception(f"Erro ao avaliar deployment {name}: ")

        for pillar, scores in summary["pillar_scores"].items():
            if scores:
                summary["pillar_scores"][pillar] = sum(scores) / len(scores)
            else:
                summary["pillar_scores"][pillar] = 0.0

        return summary

    def should_notify(
        self, scorecard: ResourceScorecard, last_notification: Optional[datetime] = None
    ) -> bool:
        if last_notification:
            cooldown = timedelta(minutes=self.config.notification_cooldown_minutes)
            if datetime.now(timezone.utc) - last_notification < cooldown:
                return False

        if scorecard.overall_score < self.config.notify_critical_threshold:
            return True
        elif scorecard.critical_issues > 0:
            return True
        elif scorecard.error_issues > 3:
            return True
        elif (
            scorecard.overall_score < self.config.notify_error_threshold
            and scorecard.error_issues > 0
        ):
            return True
        elif (
            scorecard.overall_score < self.config.notify_warning_threshold
            and scorecard.warning_issues > 5
        ):
            return True

        return False

    def get_notification_severity(self, scorecard: ResourceScorecard) -> str:
        if (
            scorecard.overall_score < self.config.notify_critical_threshold
            or scorecard.critical_issues > 0
        ):
            return "critical"
        elif (
            scorecard.overall_score < self.config.notify_error_threshold
            or scorecard.error_issues > 0
        ):
            return "error"
        elif (
            scorecard.overall_score < self.config.notify_warning_threshold
            or scorecard.warning_issues > 0
        ):
            return "warning"
        else:
            return "info"
