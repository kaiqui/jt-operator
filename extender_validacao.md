# Como Adicionar uma Nova Validação ao Scorecard

## Visão Geral
O sistema de scorecard do Titlis Operator é baseado em uma arquitetura de regras configuráveis e extensíveis. Esta documentação explica como adicionar uma nova validação (ex: "Pod rodando como non-root") ao sistema.

## Arquitetura do Sistema

### 1. **Camada de Domínio** (`./domain/models.py`)
Define a estrutura das regras de validação.

### 2. **Camada de Serviço** (`./application/services/scorecard_service.py`)
Contém a lógica principal de validação.

### 3. **Camada de Controlador** (`./controllers/scorecard_controller.py`)
Orquestra a execução e notificações.

## Passo a Passo para Adicionar uma Nova Validação

### Passo 1: Definir a Nova Regra no Domínio
Local: `./domain/models.py`

#### 1.1 Entender a Estrutura `ValidationRule`
```python
@dataclass
class ValidationRule:
    id: str                      # ID único (ex: "SEC-005")
    pillar: ValidationPillar     # Pilar (RESILIENCE, SECURITY, etc.)
    name: str                    # Nome descritivo
    description: str             # Descrição detalhada
    rule_type: ValidationRuleType # Tipo (BOOLEAN, NUMERIC, ENUM, REGEX)
    source: str                  # Fonte da regra
    # ... outros campos
```

#### 1.2 Escolher o Pilar Apropriado
- `RESILIENCE`: Alta disponibilidade, health checks, recursos
- `SECURITY`: Segurança, permissões, configurações seguras
- `PERFORMANCE`: Performance, otimização, limites
- `COST`: Custos, otimização de recursos
- `OPERATIONAL`: Operações, manutenção, logs
- `COMPLIANCE`: Conformidade, padrões, regulamentos

Para "Pod rodando como non-root", o pilar seria: **SECURITY**

### Passo 2: Adicionar a Regra à Lista de Regras Padrão
Local: `./application/services/scorecard_service.py`

#### 2.1 Localizar o Método `_get_default_rules()`
Este método retorna todas as regras padrão do sistema.

#### 2.2 Adicionar a Nova Regra à Lista
```python
ValidationRule(
    id="SEC-001",  # Usar próxima sequência disponível
    pillar=ValidationPillar.SECURITY,
    name="Pod rodando como non-root",
    description="Container deve rodar como usuário não-root",
    rule_type=ValidationRuleType.BOOLEAN,  # ou o tipo apropriado
    source="K8s API",
    severity=ValidationSeverity.ERROR,  # ou WARNING/CRITICAL
    weight=10.0,  # Peso no cálculo do score
    remediation="Configure securityContext.runAsNonRoot: true",
    documentation_url="https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted"
)
```

### Passo 3: Implementar a Lógica de Validação
Local: `./application/services/scorecard_service.py`

#### 3.1 Métodos de Validação Específicos
Existem duas abordagens:

**A) Método Genérico (Recomendado para regras simples):**
Adicionar mapeamento em `_extract_value_from_resource()`:
```python
# Adicionar ao dicionário rule_paths
"SEC-001": "spec.template.spec.containers[0].securityContext.runAsNonRoot"
```

**B) Método Específico (Para lógica complexa):**
Criar um método específico com o padrão de nome:
```python
def _validate_sec_001(self, rule: ValidationRule, resource: Dict[str, Any], 
                      namespace: str, name: str) -> ValidationResult:
    """Valida se o container está rodando como non-root."""
    # Implementação específica
```

### Passo 4: Atualizar o Sistema de Extração de Valores
Local: `./application/services/scorecard_service.py`

#### 4.1 No método `_extract_value_from_resource()`
Adicionar o caminho Kubernetes para acessar o valor:
```python
rule_paths = {
    # ... regras existentes
    "SEC-001": "spec.template.spec.containers[0].securityContext.runAsNonRoot",
}
```

### Passo 5: Testar a Implementação

#### 5.1 Teste Local
```bash
# Executar testes unitários específicos
python -m pytest tests/test_scorecard.py -k test_non_root_validation

# Executar validação manual
from src.application.services.scorecard_service import ScorecardService
service = ScorecardService()
scorecard = service.evaluate_resource("default", "my-deployment", "Deployment")
```

#### 5.2 Teste no Cluster
1. Aplicar um deployment sem `runAsNonRoot`
2. Verificar logs do operador
3. Confirmar que a validação aparece no scorecard

### Passo 6: Atualizar Documentação (Opcional)
Atualizar:
- README.md com a nova validação
- Documentação de regras de validação
- Exemplos de configuração

## Estrutura de Arquivos a Modificar

### 1. **Arquivos Principais:**
- `./domain/models.py` (se precisar adicionar novos enums ou tipos)
- `./application/services/scorecard_service.py` (principal)
- `./tests/test_scorecard.py` (testes)

### 2. **Arquivos de Configuração:**
- `./config/scorecard-rules.yaml` (se usar configuração externa)

### 3. **Arquivos de Documentação:**
- `./docs/validation-rules.md`
- `./README.md`

## Boas Práticas

### 1. **Nomenclatura:**
- IDs: `[PILAR]-[NÚMERO]` (ex: SEC-001, RES-012)
- Nomes: Descritivos e claros
- Descrições: Explicar o "porquê" além do "o que"

### 2. **Severidade:**
- `CRITICAL`: Issues que podem causar falhas graves
- `ERROR`: Issues que quebram funcionalidades
- `WARNING`: Issues que podem causar problemas
- `INFO`: Recomendações ou melhorias

### 3. **Peso:**
- 1.0-5.0: Issues menores
- 5.0-10.0: Issues importantes
- 10.0+: Issues críticas

## Exemplo Completo de Implementação

### 1. Identificar o Caminho Kubernetes
Para "Pod rodando como non-root":
```
spec.template.spec.containers[0].securityContext.runAsNonRoot
```

### 2. Determinar o Tipo de Validação
- Tipo: `ValidationRuleType.BOOLEAN`
- Valor esperado: `true`

### 3. Definir a Regra
```python
ValidationRule(
    id="SEC-001",
    pillar=ValidationPillar.SECURITY,
    name="Container Non-Root",
    description="Container não deve rodar como root",
    rule_type=ValidationRuleType.BOOLEAN,
    source="K8s API",
    severity=ValidationSeverity.ERROR,
    weight=10.0,
    remediation="Configure securityContext.runAsNonRoot: true",
    documentation_url="https://kubernetes.io/docs/tasks/configure-pod-container/security-context/"
)
```

### 4. Adicionar ao Sistema
No método `_get_default_rules()`:
```python
ValidationRule(
    id="SEC-001",
    pillar=ValidationPillar.SECURITY,
    name="Container Non-Root",
    description="Container não deve rodar como root",
    rule_type=ValidationRuleType.BOOLEAN,
    source="K8s API",
    severity=ValidationSeverity.ERROR,
    weight=10.0,
    remediation="Configure securityContext.runAsNonRoot: true",
    documentation_url="https://kubernetes.io/docs/tasks/configure-pod-container/security-context/"
)
```

### 5. Mapear o Caminho
No método `_extract_value_from_resource()`:
```python
"SEC-001": "spec.template.spec.containers[0].securityContext.runAsNonRoot"
```

## Validação de Configuração Customizada

### 1. **Via ConfigMap:**
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: titlis-scorecard-config
  namespace: titlis-system
data:
  config.yaml: |
    rules:
      - id: "SEC-001"
        enabled: true
        weight: 10.0
        severity: "error"
```

### 2. **Variáveis de Ambiente:**
```bash
TITLIS_SCORECARD_RULES='{"SEC-001": {"enabled": true, "weight": 10.0}}'
```

## Troubleshooting

### Problemas Comuns:

1. **Regra não aparece no scorecard:**
   - Verificar se `enabled=True`
   - Verificar se aplica ao tipo de recurso (`applies_to`)
   - Verificar logs do operador

2. **Validação retorna valor incorreto:**
   - Verificar caminho Kubernetes
   - Testar extração manualmente
   - Verificar estrutura do recurso

3. **Notificação não é enviada:**
   - Verificar severidade e threshold
   - Verificar se Slack está configurado
   - Verificar logs do controlador

## Recursos Adicionais

### 1. **Documentação Kubernetes:**
- [Security Context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/)
- [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)

### 2. **Exemplos de Regras:**
- Ver arquivo `./domain/models.py` para regras existentes
- Ver método `_get_default_rules()` para implementações completas

### 3. **Ferramentas de Debug:**
- `kubectl get deployment <name> -o yaml`
- `kubectl logs -n titlis-system -l app=titlis-operator`
- Testes unitários em `./tests/`

## Próximos Passos

1. **Implementar a regra** seguindo os passos acima
2. **Testar localmente** com um deployment de teste
3. **Validar no cluster** de desenvolvimento
4. **Documentar a regra** para outros desenvolvedores
5. **Monitorar** o impacto da nova validação

## Suporte
Para dúvidas ou problemas, consulte:
- Arquitetura do código existente
- Regras similares já implementadas
- Logs do operador
- Documentação do Kubernetes