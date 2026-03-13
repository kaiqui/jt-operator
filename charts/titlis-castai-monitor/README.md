# titlis-castai-monitor

Chart Helm dedicado para rodar **apenas** o módulo de monitoramento do agente CAST AI.

Todos os outros controllers do Titlis Operator (Scorecard, SLO, Auto-Remediação, Backstage, GitHub, Slack) ficam **desabilitados** neste chart.

---

## Pré-requisitos

- Kubernetes >= 1.24
- Helm >= 3.10
- [External Secrets Operator](https://external-secrets.io/) instalado no cluster (se usar `externalSecret.enabled=true`)
- Secret `titlis-datadog-keys` com as chaves `api-key` e `app-key` do Datadog

---

## Secret necessária

O único secret obrigatório é o do Datadog, usado para enviar as métricas de health do castai-agent:

```
titlis-datadog-keys
  api-key: <DD_API_KEY>
  app-key: <DD_APP_KEY>
```

Você pode criá-la de duas formas:

### Opção A — ExternalSecret (recomendado)

Habilite no values e configure o caminho no seu provider:

```yaml
externalSecret:
  enabled: true
  refreshInterval: "1h"
  secretStore:
    name: "cluster-secret-store"
    kind: ClusterSecretStore
  datadogPath: "/titlis/datadog"   # ajuste para o path do seu provider
```

O ExternalSecret espera que o segredo remoto tenha as propriedades `api_key` e `app_key`.

**Exemplos de path por provider:**

| Provider | Exemplo de `datadogPath` |
|---|---|
| AWS SSM Parameter Store | `/titlis/datadog` |
| AWS Secrets Manager | `titlis/datadog` |
| HashiCorp Vault | `secret/data/titlis/datadog` |
| GCP Secret Manager | `titlis-datadog` |

### Opção B — Secret manual

```bash
kubectl create secret generic titlis-datadog-keys \
  --namespace titlis \
  --from-literal=api-key="<DD_API_KEY>" \
  --from-literal=app-key="<DD_APP_KEY>"
```

---

## Instalação

### 1. Criar o namespace

```bash
kubectl create namespace titlis
```

### 2. (Opção A) Instalar com ExternalSecret

```bash
helm upgrade --install titlis-castai-monitor charts/titlis-castai-monitor \
  --namespace titlis \
  --set castai.clusterName=meu-cluster \
  --set castai.monitorNamespace=castai-agent \
  --set castai.monitorIntervalSeconds=60 \
  --set externalSecret.enabled=true \
  --set externalSecret.secretStore.name=cluster-secret-store \
  --set externalSecret.datadogPath="/titlis/datadog"
```

### 2. (Opção B) Instalar com secret manual

Crie o secret antes (veja Opção B acima) e depois:

```bash
helm upgrade --install titlis-castai-monitor charts/titlis-castai-monitor \
  --namespace titlis \
  --set castai.clusterName=meu-cluster \
  --set castai.monitorNamespace=castai-agent \
  --set castai.monitorIntervalSeconds=60
```

---

## Configuração

| Parâmetro | Descrição | Default |
|---|---|---|
| `image.repository` | Imagem do operator | `kailima/titlis-operator` |
| `image.tag` | Tag da imagem | `latest` |
| `castai.clusterName` | Nome do cluster no CAST AI | `develop` |
| `castai.monitorNamespace` | Namespace onde o castai-agent roda | `castai-agent` |
| `castai.monitorIntervalSeconds` | Intervalo entre verificações (segundos) | `60` |
| `datadog.site` | Site do Datadog | `datadoghq.com` |
| `datadog.secretName` | Nome do secret com as chaves do Datadog | `titlis-datadog-keys` |
| `externalSecret.enabled` | Habilita criação via ExternalSecret | `false` |
| `externalSecret.refreshInterval` | Frequência de sincronização do secret | `1h` |
| `externalSecret.secretStore.name` | Nome do SecretStore/ClusterSecretStore | `cluster-secret-store` |
| `externalSecret.secretStore.kind` | Tipo: `SecretStore` ou `ClusterSecretStore` | `ClusterSecretStore` |
| `externalSecret.datadogPath` | Caminho do segredo no provider | `/titlis/datadog` |
| `logging.level` | Nível de log (`DEBUG`, `INFO`, `WARNING`) | `INFO` |
| `operator.leaderElection.enabled` | Habilita leader election (necessário com >1 réplica) | `true` |

---

## O que este chart NÃO inclui

- CRDs do Titlis (`AppScorecard`, `AppRemediation`, `SLOConfig`) — não são necessários
- Secrets de Slack, GitHub, Backstage
- RBAC para deployments, HPAs, CRDs do titlis

---

## Verificar funcionamento

```bash
# Ver logs do pod
kubectl logs -n titlis -l app.kubernetes.io/name=titlis-castai-monitor -f

# Confirmar feature flags
kubectl get configmap -n titlis titlis-castai-monitor-config -o yaml

# Verificar ExternalSecret (se habilitado)
kubectl get externalsecret -n titlis
kubectl get secret titlis-datadog-keys -n titlis
```

Os logs de um ciclo saudável se parecem com:

```json
{"event": "Iniciando verificação de health CAST AI", "cluster_name": "meu-cluster", "namespace": "castai-agent"}
{"event": "Ciclo de monitoramento CAST AI concluído", "healthy": ["castai-agent", "castai-spot-handler"], "unhealthy": []}
```
