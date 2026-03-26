# Titlis Operator — Evoluções com Inteligência Artificial

> **Audiência:** Gerentes e Diretores
> **Data:** Março 2026
> **Objetivo:** Documentar as capacidades inteligentes entregues pelo Titlis Operator — o que resolve, como funciona e como o impacto é medido.

---

## Visão Geral

O Titlis Operator é um sistema que roda dentro do cluster Kubernetes e age como um auditor e corretor automático de workloads. As três evoluções documentadas aqui utilizam inteligência — coleta de sinais, análise de padrões e decisão automatizada — para reduzir trabalho manual, prevenir falhas e garantir conformidade contínua sem intervenção humana.

---

## Evolução 1 — Scorecard Inteligente de Maturidade de Workloads

### A Dor

Equipes de engenharia não sabem, em tempo real, quais serviços estão mal configurados do ponto de vista de resiliência, segurança e performance. A descoberta acontece apenas durante incidentes — quando já é tarde.

Auditorias manuais são esporádicas, subjetivas e não escalam com o crescimento do número de serviços. Um engenheiro leva entre 30 minutos e 2 horas para avaliar um único Deployment manualmente.

### Como Resolve

O operador avalia automaticamente cada Deployment criado ou atualizado no cluster. São **26 regras** cobrindo 4 pilares:

| Pilar | Exemplos de Regras |
|-------|-------------------|
| **Resiliência** | Liveness/Readiness Probe, CPU/Memória configurados, HPA ativo |
| **Segurança** | Imagem sem tag `latest`, container não-root, privilege escalation desabilitado |
| **Performance** | Limites de recurso proporcionais, HPA com targets adequados |
| **Operacional** | Graceful shutdown, rollout strategy configurado |

Para cada Deployment, o sistema:
1. Executa todas as regras em menos de 1 segundo
2. Calcula um score de 0 a 100 por pilar
3. Persiste o resultado num Custom Resource (`AppScorecard`) visível via `kubectl`
4. Envia um digest consolidado por namespace no Slack

O resultado fica disponível para qualquer time — engenharia, SRE, plataforma — sem acesso ao operador em si.

### Como o KPI é Medido

| KPI | Método de Coleta | Meta |
|-----|-----------------|------|
| **Score médio por namespace** | `AppScorecard.overall_score` — consultado via `kubectl get appscorecards` ou Grafana | ≥ 70 pontos |
| **% de workloads com score crítico (< 50)** | Query sobre `AppScorecard.overall_score < 50` | < 10% do total |
| **Tempo até identificação de não-conformidade** | Timestamp do evento K8s vs timestamp do `AppScorecard` criado | < 60 segundos |
| **Cobertura de avaliação** | `(workloads avaliados / workloads totais) × 100` | 100% |
| **Redução de tempo de auditoria manual** | Comparação de horas de engenharia gastas em auditorias antes/depois | > 80% de redução |

---

## Evolução 2 — Auto-Remediação por Pull Request

### A Dor

Quando um serviço está sem CPU request definido, sem HPA ou com limites de memória inadequados, o engenheiro responsável precisa: identificar o problema, medir o uso real no Datadog, calcular o valor correto, editar o manifesto YAML, abrir um PR, aguardar review e fazer merge. Esse ciclo leva dias — e muitas vezes não acontece porque o problema "não quebrou nada ainda".

O resultado é acúmulo silencioso de dívida técnica de configuração que vira risco em picos de tráfego.

### Como Resolve

Quando o Scorecard detecta problemas **remediáveis** (CPU, memória, HPA), o operador executa automaticamente um pipeline de remediação:

```
Problema detectado no Scorecard
        │
        ▼
Coleta métricas reais de CPU e memória no Datadog (últimas 24h)
        │
        ▼
Calcula valores sugeridos — NUNCA reduz o que já está configurado
        │
        ▼
Clona o repositório GitHub do serviço
        │
        ▼
Modifica o deploy.yaml com os valores calculados
        │
        ▼
Cria branch + commit + Pull Request no GitHub
        │
        ▼
Notifica o time no Slack com link para o PR
```

**Garantias do sistema:**
- **Nunca reduz** valores existentes — só sugere melhorias (princípio "immutable floor")
- **Sem duplicidade** — antes de abrir um novo PR, verifica se já existe um aberto para o mesmo serviço
- **Rastreável** — cada remediação gera um `AppRemediation` CRD persistente com status, URL do PR e issues identificadas

**Regras remediáveis automaticamente:**

| Categoria | Regras |
|-----------|--------|
| Recursos | CPU Requests, CPU Limits, Memory Requests, Memory Limits |
| Escalabilidade | HPA ausente, HPA sem targets adequados |

### Como o KPI é Medido

| KPI | Método de Coleta | Meta |
|-----|-----------------|------|
| **PRs de remediação criados** | `AppRemediation.status = created` — contagem mensal | Crescente até zerar backlog |
| **Taxa de merge dos PRs** | `(PRs merged / PRs criados) × 100` — via GitHub API | > 80% |
| **Tempo médio de ciclo** | Timestamp detecção → merge do PR | < 48 horas |
| **Redução de issues por remediação aceita** | Delta no score do `AppScorecard` antes/depois do merge | Score aumenta ≥ 10 pontos |
| **% de workloads sem CPU/memória configurados** | Query sobre regras RES-003, RES-004, RES-005, RES-006 falhando | < 5% do total |
| **Horas de engenharia poupadas** | Estimativa: 1h por PR que não precisou ser feito manualmente | Baseline: 0h → Meta: > 40h/mês poupadas |

---

## Evolução 3 — Detecção Inteligente de Framework para SLOs

### A Dor

SLOs (Service Level Objectives) no Datadog precisam ser configurados com queries específicas para cada framework de aplicação. Um serviço FastAPI tem uma query diferente de um serviço WSGI ou aiohttp. Configurar isso manualmente para dezenas de serviços é propenso a erro — o SLO fica medindo a coisa errada e dá uma falsa sensação de conformidade.

### Como Resolve

Quando um `SLOConfig` é criado com `auto_detect_framework: true`, o operador executa uma **cadeia de detecção inteligente** com três fontes de sinal, em ordem de precedência:

```
Fonte 1 — Annotation Kubernetes (maior precedência)
  titlis.io/app-framework: fastapi
        │ (se não encontrada)
        ▼
Fonte 2 — Datadog Service Definition
  tags: [framework:fastapi]
        │ (se não encontrada)
        ▼
Fonte 3 — Fallback WSGI (padrão mais conservador)
```

O framework detectado é:
- Persistido no status do CRD (`status.detected_framework` + `detection_source`)
- Usado para construir automaticamente as queries corretas do SLO no Datadog
- Logado de forma estruturada para auditoria

Isso garante que o SLO criado mede exatamente o que deve medir para aquele serviço, sem trabalho manual de configuração de queries.

**Idempotência inteligente (Three-Path Reconciliation):**

O sistema também garante que o mesmo SLO nunca seja criado duas vezes, mesmo após reinicializações do operador:

| Caminho | Condição | Ação |
|---------|----------|------|
| **Path A** | `slo_id` já conhecido no status | Atualiza direto, sem busca no Datadog |
| **Path B** | Sem `slo_id`, mas tem `resource_uid` | Busca por tag `titlis_resource_uid` antes de criar |
| **Path C** | Fluxo inicial | Busca por nome → atualiza ou cria → grava tag de rastreio |

### Como o KPI é Medido

| KPI | Método de Coleta | Meta |
|-----|-----------------|------|
| **% de SLOs com framework correto** | `detection_source != "fallback"` / total de SLOs | > 90% |
| **% de SLOs duplicados criados** | `AppRemediation.error contains "duplicate"` — deveria ser zero | 0% |
| **Tempo de provisionamento de SLO** | Timestamp criação do CRD → SLO ativo no Datadog | < 120 segundos |
| **% de serviços usando `latest` como fallback (WSGI inesperado)** | `status.detection_source = "fallback"` | < 10% — indica necessidade de annotation |
| **Redução de configuração manual de SLO** | Comparação de tickets de "configurar SLO" antes/depois | > 70% de redução |

---

## Resumo Executivo — Impacto Combinado

| Capacidade | Problema Resolvido | Ganho Direto |
|-----------|-------------------|-------------|
| **Scorecard Inteligente** | Falta de visibilidade contínua de conformidade | Auditoria de 26 regras em < 1s por serviço, sem esforço humano |
| **Auto-Remediação por PR** | Ciclo lento de correção manual de configuração | PRs automáticos em < 5 minutos após detecção, baseados em dados reais do Datadog |
| **Detecção de Framework SLO** | Erro humano na configuração de queries de SLO | SLOs provisionados com queries corretas por inferência automática |

### Estimativa de Impacto Operacional

| Métrica | Antes | Com Titlis Operator |
|---------|-------|---------------------|
| Tempo para detectar workload mal configurado | Horas a dias (incidente) | < 60 segundos |
| Horas de engenharia por auditoria manual | 2–4h por serviço | 0h (automatizado) |
| Ciclo de correção de configuração | 2–5 dias (detecção → PR → merge) | < 48h (PR automático) |
| Configuração de SLO por serviço | 30–60 min/serviço | < 2 min (auto-detect + provision) |

---

## Dashboard Recomendado (Grafana / Datadog)

Para acompanhamento semanal pelos times de gestão, recomenda-se um painel com:

1. **Score médio por namespace** — tendência ao longo do tempo
2. **Funil de remediação** — Problemas detectados → PRs criados → PRs merged
3. **Distribuição de SLOs por detection_source** — annotation vs datadog_tag vs fallback
4. **Top 10 regras que mais falham** — identifica onde concentrar esforço de melhoria
5. **Workloads críticos (score < 50)** — lista acionável para priorização

---

*Documento gerado com base na arquitetura implementada em `src/` e roadmap em `docs/rules-and-evolution.md`.*
*Próxima revisão: após entrega da Fase 1 (Foundation SaaS — Q1 2026).*
