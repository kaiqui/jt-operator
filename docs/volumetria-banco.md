# Volumetria do Banco de Dados — Titlis Operator

> Análise baseada no comportamento real do operador (event-driven via Kopf) e na
> configuração atual de infraestrutura: 2 clusters, 3 ambientes (dev/hml/prod),
> média de 100 apps por ambiente.

---

## Premissas

| Parâmetro | Valor |
|---|---|
| Clusters | 2 |
| Ambientes por cluster | 3 (dev / hml / prod) |
| Apps por ambiente | ~100 |
| **Total de workloads monitorados** | **~600** |
| SLOConfigs estimados (1 SLO por app) | ~600 |
| Regras de validação por avaliação | 26 |
| Pilares por avaliação | 6 |

---

## Modelo de disparo

O scorecard **não roda em ciclo periódico fixo** — é **puramente event-driven via Kopf**:

| Evento | Frequência | Workloads afetados |
|---|---|---|
| `kopf.on.resume` | A cada restart do operador | Todos os 600 de uma vez (burst) |
| `kopf.on.update` | A cada mudança de spec no K8s | 1 por evento |
| `kopf.on.create` | Novo deployment | 1 por evento |
| `kopf.on.delete` | Deleção | Apenas notificação Slack |

> `RECONCILE_INTERVAL_SECONDS=300` está configurado mas não está sendo usado por um
> timer ativo no código atual — pode ser referência para uso futuro.

---

## Tabelas OLTP — volume estável

Estas tabelas têm **número de linhas com teto fixo**, mas alta taxa de UPDATE/UPSERT.

| Tabela | Linhas (teto) | Crescimento de linhas | Churn diário |
|---|---|---|---|
| `workloads` | ~600 | +5–10/semana | Baixo |
| `app_scorecards` | ~600 | Estável | ~1.500 UPDATEs/dia |
| `pillar_scores` | ~3.600 (600×6) | Estável | ~9.000 upserts/dia |
| `validation_results` | ~15.600 (600×26) | Estável | ~39.000 upserts/dia |
| `app_remediations` | < 600 | Muito baixo | Poucos UPDATEs/dia |
| `remediation_issues` | < 3.000 | Baixo | Poucos inserts/dia |
| `slo_configs` | ~600 | Estável | Muda quando CRDs mudam |
| `namespaces` | ~6 (2×3) | Estável | — |
| `clusters` | 2 | Estável | — |

**Tamanho total do schema `titlis_oltp`: ~50 MB** — independente do tempo de operação.

---

## Tabelas AUDIT — crescimento linear (as mais críticas)

Crescem indefinidamente. O volume depende da política de escrita da aplicação.

### Cenário A — escreve no histórico em toda avaliação (pior caso)

| Tabela | Inserts/dia | Tamanho/dia | Por mês | Por ano |
|---|---|---|---|---|
| `app_scorecard_history` | ~1.500 | ~3 MB | ~90 MB | ~1,1 GB |
| `pillar_score_history` | ~9.000 | ~2 MB | ~60 MB | ~730 MB |
| `slo_compliance_history` | ~600–1.800 | ~0,5 MB | ~15 MB | ~180 MB |
| `notification_log` | ~100–300 | insignificante | ~5 MB | ~60 MB |
| `remediation_history` | ~20–50 | insignificante | < 1 MB | ~5 MB |

### Cenário B — escreve no histórico apenas quando o score mudou (recomendado)

Assumindo que 70–80% das avaliações não alteram o score em infraestrutura estável:

| Tabela | Inserts/dia | Por mês | Por ano |
|---|---|---|---|
| `app_scorecard_history` | ~300–400 | ~18 MB | ~220 MB |
| `pillar_score_history` | ~1.800–2.400 | ~12 MB | ~145 MB |

> **Recomendação:** a aplicação deve incrementar `version` apenas quando o scorecard
> mudar de fato, não em todo evento de reconciliação. Essa decisão tem impacto de
> **4–5× no crescimento** de `app_scorecard_history`.

---

## Tabelas TS — crescimento append-only

| Tabela | Quando cresce | Inserts/dia | Por mês | Por ano |
|---|---|---|---|---|
| `scorecard_scores` | A cada avaliação registrada | ~1.500 | ~13 MB | ~160 MB |
| `resource_metrics` | Apenas em avaliações que consultam Datadog (remediação) | ~50–200 | ~1 MB | ~12 MB |

`scorecard_scores` é a tabela **candidata prioritária a particionamento trimestral**.

---

## Ranking de crescimento

```
MAIOR CRESCIMENTO
┌──────────────────────────────────────────────────────────┐
│  1. titlis_audit.app_scorecard_history   ~1,1 GB/ano *   │  ← histórico JSONB
│  2. titlis_audit.pillar_score_history    ~730 MB/ano *   │  ← detalhe por pilar
│  3. titlis_audit.slo_compliance_history  ~180 MB/ano     │  ← sync com Datadog
│  4. titlis_ts.scorecard_scores           ~160 MB/ano     │  ← série temporal
│  5. titlis_audit.notification_log        ~60 MB/ano      │
│  6. titlis_audit.remediation_history     ~5 MB/ano       │
│  7. titlis_oltp.*  (todas)               ~50 MB total    │  ← tamanho estável
└──────────────────────────────────────────────────────────┘
MENOR CRESCIMENTO

* Cenário A (toda avaliação gera histórico). Divide por 4–5× no Cenário B.
```

---

## Projeção acumulada — 3 anos (Cenário A)

| Tabela | Ano 1 | Ano 2 | Ano 3 |
|---|---|---|---|
| `app_scorecard_history` | 1,1 GB | 2,2 GB | 3,3 GB |
| `pillar_score_history` | 730 MB | 1,5 GB | 2,2 GB |
| `slo_compliance_history` | 180 MB | 360 MB | 540 MB |
| `scorecard_scores` | 160 MB | 320 MB | 480 MB |
| `notification_log` | 60 MB | 120 MB | 180 MB |
| `remediation_history` | 5 MB | 10 MB | 15 MB |
| **Total audit + ts** | **~2,2 GB** | **~4,4 GB** | **~6,6 GB** |

> Se o número de workloads crescer (novos clusters ou ambientes), o volume escala
> linearmente: dobrar workloads = dobrar volume de audit/ts.

---

## Pico de burst (restart do operador)

O `kopf.on.resume` dispara **600 avaliações simultâneas** ao reiniciar o operador.
A API deve estar preparada para absorver ~600 inserts em < 30 segundos.
Não é um problema de volume total, mas de **taxa momentânea de escrita**.

---

## Recomendações

| # | Ação | Quando |
|---|---|---|
| 1 | **Particionamento trimestral** em `titlis_audit.*` e `titlis_ts.*` via `pg_partman` | Antes de 6 meses de dados |
| 2 | **Política de retenção**: 12–18 meses para `titlis_audit.*`; 3–6 meses para `titlis_ts.scorecard_scores` | Junto com o particionamento |
| 3 | **Incrementar `version` apenas em mudanças reais** na camada de aplicação — reduz volume de histórico em 4–5× | Na implementação da integração |
| 4 | **TimescaleDB** para `titlis_ts.*` — compressão nativa pode reduzir volume em ~90% para dados com mais de 7 dias | Avaliação futura |
| 5 | **Rate limiting na API** para absorver burst de restart (600 inserts simultâneos) | Na implementação da integração |
