# Modelagem Relacional — Titlis Operator

> **DBA Specialist / Arquiteto de Dados Sênior**
> Estratégia: SCD Type 4 + Append-only Time-series | PostgreSQL 15+

> **Status do banco:** Schema criado em [`db/schema.sql`](../db/schema.sql) — **não integrado ao operador**.
> O operador ainda persiste estado exclusivamente via CRDs Kubernetes (`AppScorecard`, `AppRemediation`, `SLOConfig`).
> A integração com o banco de dados é planejada como fase futura (ver Schema Evolution).

---

## Estratégia Arquitetural

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATABASE ARCHITECTURE                         │
│                                                                 │
│  titlis_oltp          titlis_audit          titlis_ts           │
│  ─────────────        ─────────────         ─────────           │
│  Estado atual         SCD Type 4            Append-only         │
│  (leitura rápida)     (versionamento)       (time-series)       │
│                                                                 │
│  Frontend/APIs   ←─── Aplicação (audit trail) ──→  Dashboards   │
└─────────────────────────────────────────────────────────────────┘
```

**Decisão central:** SCD Type 4 (tabela corrente + tabela histórica separada) foi escolhido sobre SCD Type 2 porque:
- O frontend precisa de uma row única por workload sem filtrar por `is_current = true`
- Queries OLAP no histórico não competem com writes no OLTP
- Snapshots JSONB em `*_history` eliminam joins custosos para análise

---

## DDL Completo

> **Atenção:** O DDL canônico e atualizado está em [`db/schema.sql`](../db/schema.sql).
> O bloco abaixo é uma versão de referência para leitura contextual — pode estar desatualizado.
> Para criar o banco, use sempre `db/schema.sql`.
>
> **Padrões adotados na revisão DBA:**
> - PKs no formato `<nome_da_tabela>_id` com tipo `BIGINT GENERATED ALWAYS AS IDENTITY`
> - Nomes de colunas compostos (evita `name`, `type`, `status` standalone)
> - `VARCHAR(n)` quando tamanho máximo é conhecido; `TEXT` apenas quando indefinido
> - `COMMENT ON TABLE/COLUMN` em todas as tabelas e colunas
> - Sem triggers DML — `updated_at` e audit trail gerenciados pela aplicação

```sql
-- ================================================================
-- TITLIS OPERATOR — MODELAGEM RELACIONAL
-- PostgreSQL 15+ | Estratégia: SCD Type 4 + Append-only Time-series
-- ================================================================

-- ================================================================
-- SCHEMAS
-- ================================================================
CREATE SCHEMA IF NOT EXISTS titlis_oltp;   -- Estado atual (Frontend/APIs)
CREATE SCHEMA IF NOT EXISTS titlis_audit;  -- Histórico e auditoria (SCD Type 4)
CREATE SCHEMA IF NOT EXISTS titlis_ts;     -- Métricas time-series (append-only)

-- ================================================================
-- ENUMS (domain types espelhados do Python)
-- ================================================================
CREATE TYPE titlis_oltp.compliance_status AS ENUM (
    'COMPLIANT', 'NON_COMPLIANT', 'UNKNOWN', 'PENDING'
);

CREATE TYPE titlis_oltp.service_tier AS ENUM (
    'TIER_1', 'TIER_2', 'TIER_3', 'TIER_4'
);

CREATE TYPE titlis_oltp.validation_pillar AS ENUM (
    'RESILIENCE', 'SECURITY', 'COST', 'PERFORMANCE', 'OPERATIONAL', 'COMPLIANCE'
);

CREATE TYPE titlis_oltp.validation_severity AS ENUM (
    'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'OPTIONAL'
);

CREATE TYPE titlis_oltp.validation_rule_type AS ENUM (
    'BOOLEAN', 'NUMERIC', 'ENUM', 'REGEX'
);

CREATE TYPE titlis_oltp.remediation_status AS ENUM (
    'PENDING', 'IN_PROGRESS', 'PR_OPEN', 'PR_MERGED', 'FAILED', 'SKIPPED'
);

CREATE TYPE titlis_oltp.slo_type AS ENUM (
    'METRIC', 'MONITOR', 'TIME_SLICE'
);

CREATE TYPE titlis_oltp.slo_timeframe AS ENUM (
    '7d', '30d', '90d'
);

CREATE TYPE titlis_oltp.slo_state AS ENUM (
    'ok', 'warning', 'error', 'no_data'
);

CREATE TYPE titlis_oltp.notification_severity AS ENUM (
    'INFO', 'WARNING', 'ERROR', 'CRITICAL'
);

CREATE TYPE titlis_oltp.remediation_category AS ENUM (
    'resources', 'hpa'
);

-- ================================================================
-- SCHEMA: titlis_oltp — Estado Atual (OLTP)
-- ================================================================

-- ----------------------------------------------------------------
-- clusters
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.clusters (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL UNIQUE,
    environment     VARCHAR(100) NOT NULL,           -- production, staging, develop
    region          VARCHAR(100),
    provider        VARCHAR(100),                    -- aws, gcp, azure, on-prem
    k8s_version     VARCHAR(50),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------
-- namespaces
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.namespaces (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id      UUID        NOT NULL REFERENCES titlis_oltp.clusters(id),
    name            VARCHAR(255) NOT NULL,
    is_excluded     BOOLEAN     NOT NULL DEFAULT FALSE,  -- excluded_namespaces config
    labels          JSONB,
    annotations     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cluster_id, name)
);

-- ----------------------------------------------------------------
-- workloads  (espelha Kubernetes Deployments)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.workloads (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id            UUID        NOT NULL REFERENCES titlis_oltp.namespaces(id),
    name                    VARCHAR(255) NOT NULL,
    kind                    VARCHAR(100) NOT NULL DEFAULT 'Deployment',
    service_tier            titlis_oltp.service_tier,
    dd_git_repository_url   TEXT,        -- DD_GIT_REPOSITORY_URL (pré-condição de remediação)
    backstage_component     VARCHAR(255),
    owner_team              VARCHAR(255),
    labels                  JSONB,
    annotations             JSONB,
    resource_version        VARCHAR(100), -- K8s resourceVersion
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, name, kind)
);

-- ----------------------------------------------------------------
-- validation_rules  (catálogo das 26+ regras — referência imutável)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.validation_rules (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id              VARCHAR(50) NOT NULL UNIQUE,  -- RES-001, PERF-002...
    pillar               titlis_oltp.validation_pillar   NOT NULL,
    severity             titlis_oltp.validation_severity NOT NULL,
    rule_type            titlis_oltp.validation_rule_type NOT NULL,
    weight               NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    name                 VARCHAR(255) NOT NULL,
    description          TEXT,
    is_remediable        BOOLEAN     NOT NULL DEFAULT FALSE,
    remediation_category titlis_oltp.remediation_category,  -- resources | hpa
    is_active            BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------
-- app_scorecards  (estado atual — SCD Type 4 "current table")
-- Constraint UNIQUE(workload_id) garante 1 linha por workload.
-- Antes de UPDATE, trigger copia o estado anterior para app_scorecard_history.
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.app_scorecards (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id       UUID        NOT NULL REFERENCES titlis_oltp.workloads(id),
    version           INTEGER     NOT NULL DEFAULT 1,   -- incrementa a cada nova avaliação
    overall_score     NUMERIC(5,2) NOT NULL CHECK (overall_score BETWEEN 0 AND 100),
    compliance_status titlis_oltp.compliance_status NOT NULL DEFAULT 'UNKNOWN',
    total_rules       INTEGER     NOT NULL DEFAULT 0,
    passed_rules      INTEGER     NOT NULL DEFAULT 0,
    failed_rules      INTEGER     NOT NULL DEFAULT 0,
    critical_failures INTEGER     NOT NULL DEFAULT 0,
    error_count       INTEGER     NOT NULL DEFAULT 0,
    warning_count     INTEGER     NOT NULL DEFAULT 0,
    evaluated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    k8s_event_type    VARCHAR(50),                      -- resume | create | update
    raw_metadata      JSONB,                            -- campos extras do K8s body
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workload_id)                                -- 1 scorecard atual por workload
);

-- ----------------------------------------------------------------
-- pillar_scores  (scores por pilar — estado atual)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.pillar_scores (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scorecard_id   UUID        NOT NULL REFERENCES titlis_oltp.app_scorecards(id) ON DELETE CASCADE,
    pillar         titlis_oltp.validation_pillar NOT NULL,
    score          NUMERIC(5,2) NOT NULL CHECK (score BETWEEN 0 AND 100),
    passed_checks  INTEGER     NOT NULL DEFAULT 0,
    failed_checks  INTEGER     NOT NULL DEFAULT 0,
    weighted_score NUMERIC(8,4),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scorecard_id, pillar)
);

-- ----------------------------------------------------------------
-- validation_results  (resultado por regra — estado atual)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.validation_results (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scorecard_id UUID        NOT NULL REFERENCES titlis_oltp.app_scorecards(id) ON DELETE CASCADE,
    rule_id      UUID        NOT NULL REFERENCES titlis_oltp.validation_rules(id),
    passed       BOOLEAN     NOT NULL,
    message      TEXT,
    actual_value TEXT,                                  -- valor observado no Deployment
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scorecard_id, rule_id)
);

-- ----------------------------------------------------------------
-- app_remediations  (estado atual — SCD Type 4 "current table")
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.app_remediations (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id      UUID        NOT NULL REFERENCES titlis_oltp.workloads(id),
    version          INTEGER     NOT NULL DEFAULT 1,
    scorecard_id     UUID        REFERENCES titlis_oltp.app_scorecards(id),
    status           titlis_oltp.remediation_status NOT NULL DEFAULT 'PENDING',
    github_pr_number INTEGER,
    github_pr_url    TEXT,
    github_pr_title  TEXT,
    github_branch    TEXT,
    repository_url   TEXT,
    error_message    TEXT,
    triggered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workload_id)                                -- 1 remediação ativa por workload
);

-- ----------------------------------------------------------------
-- remediation_issues  (issues individuais vinculadas à remediação)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.remediation_issues (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    remediation_id  UUID        NOT NULL REFERENCES titlis_oltp.app_remediations(id) ON DELETE CASCADE,
    rule_id         UUID        NOT NULL REFERENCES titlis_oltp.validation_rules(id),
    category        titlis_oltp.remediation_category NOT NULL,
    description     TEXT,
    suggested_value TEXT,       -- valor sugerido pelo operador
    applied_value   TEXT,       -- valor efetivamente aplicado no PR (pode diferir por _keep_max)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------
-- slo_configs  (estado atual dos SLOs — espelha SLOConfig CRD)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.slo_configs (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id      UUID        NOT NULL REFERENCES titlis_oltp.namespaces(id),
    name              VARCHAR(255) NOT NULL,
    slo_type          titlis_oltp.slo_type      NOT NULL,
    timeframe         titlis_oltp.slo_timeframe NOT NULL,
    target            NUMERIC(6,4) NOT NULL CHECK (target BETWEEN 0 AND 100),
    warning           NUMERIC(6,4)              CHECK (warning BETWEEN 0 AND 100),
    datadog_slo_id    VARCHAR(255),             -- ID gerado no Datadog após criação
    datadog_slo_state titlis_oltp.slo_state,
    last_sync_at      TIMESTAMPTZ,
    sync_error        TEXT,
    spec_raw          JSONB,                    -- spec completo do CRD para auditoria
    version           INTEGER     NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, name),
    CONSTRAINT chk_warning_lt_target CHECK (warning IS NULL OR warning < target)
);

-- ================================================================
-- SCHEMA: titlis_audit — Histórico e Auditoria (SCD Type 4)
-- ================================================================

-- ----------------------------------------------------------------
-- app_scorecard_history
-- Sem FK para workload_id — registros históricos devem sobreviver
-- mesmo se o workload for deletado (soft-delete em workloads).
-- Snapshot JSONB de pillar_scores e validation_results elimina
-- joins custosos em queries analíticas.
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.app_scorecard_history (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id        UUID        NOT NULL,        -- ref lógica sem FK constraint
    scorecard_version  INTEGER     NOT NULL,
    overall_score      NUMERIC(5,2) NOT NULL,
    compliance_status  VARCHAR(50) NOT NULL,
    total_rules        INTEGER     NOT NULL,
    passed_rules       INTEGER     NOT NULL,
    failed_rules       INTEGER     NOT NULL,
    critical_failures  INTEGER     NOT NULL,
    error_count        INTEGER     NOT NULL,
    warning_count      INTEGER     NOT NULL,
    pillar_scores      JSONB       NOT NULL,        -- [{pillar, score, passed, failed}]
    validation_results JSONB       NOT NULL,        -- [{rule_id, rule_ref, pillar, severity, passed, message, actual_value}]
    evaluated_at       TIMESTAMPTZ NOT NULL,
    k8s_event_type     VARCHAR(50),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_scorecard_hist_workload_time
    ON titlis_audit.app_scorecard_history (workload_id, evaluated_at DESC);

CREATE INDEX idx_scorecard_hist_compliance
    ON titlis_audit.app_scorecard_history (compliance_status, evaluated_at DESC);

CREATE INDEX idx_scorecard_hist_pillar_gin
    ON titlis_audit.app_scorecard_history USING GIN (pillar_scores);

CREATE INDEX idx_scorecard_hist_validation_gin
    ON titlis_audit.app_scorecard_history USING GIN (validation_results);

-- ----------------------------------------------------------------
-- pillar_score_history  (granularidade fina por pilar — para gráficos de evolução)
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.pillar_score_history (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id    UUID        NOT NULL,
    scorecard_version INTEGER  NOT NULL,
    pillar         VARCHAR(50) NOT NULL,
    score          NUMERIC(5,2) NOT NULL,
    passed_checks  INTEGER     NOT NULL,
    failed_checks  INTEGER     NOT NULL,
    weighted_score NUMERIC(8,4),
    evaluated_at   TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_pillar_hist_workload_pillar_time
    ON titlis_audit.pillar_score_history (workload_id, pillar, evaluated_at DESC);

-- ----------------------------------------------------------------
-- remediation_history  (log de todas as transições de estado)
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.remediation_history (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id         UUID        NOT NULL,
    remediation_version INTEGER     NOT NULL,
    status              VARCHAR(50) NOT NULL,
    previous_status     VARCHAR(50),                 -- para rastrear transição de estado
    scorecard_version   INTEGER,
    github_pr_number    INTEGER,
    github_pr_url       TEXT,
    github_branch       TEXT,
    repository_url      TEXT,
    issues_snapshot     JSONB,                       -- snapshot das issues no momento
    error_message       TEXT,
    triggered_at        TIMESTAMPTZ NOT NULL,
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_remediation_hist_workload_time
    ON titlis_audit.remediation_history (workload_id, triggered_at DESC);

CREATE INDEX idx_remediation_hist_status
    ON titlis_audit.remediation_history (status, created_at DESC);

-- ----------------------------------------------------------------
-- slo_compliance_history  (histórico de conformidade e sincronização)
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.slo_compliance_history (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slo_config_id   UUID        NOT NULL,            -- ref lógica
    namespace_id    UUID        NOT NULL,
    slo_name        VARCHAR(255) NOT NULL,
    datadog_slo_id  VARCHAR(255),
    slo_type        VARCHAR(50) NOT NULL,
    timeframe       VARCHAR(10) NOT NULL,
    target          NUMERIC(6,4) NOT NULL,
    actual_value    NUMERIC(6,4),                    -- compliance % real do Datadog
    slo_state       VARCHAR(50),
    sync_action     VARCHAR(50),                     -- created | updated | noop | error
    sync_error      TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_slo_hist_config_time
    ON titlis_audit.slo_compliance_history (slo_config_id, recorded_at DESC);

-- ----------------------------------------------------------------
-- notification_log  (auditoria de todas as notificações Slack)
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.notification_log (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id       UUID,                          -- NULL se digest de namespace
    namespace_id      UUID,
    notification_type VARCHAR(50) NOT NULL,          -- scorecard | remediation | digest | slo
    severity          titlis_oltp.notification_severity NOT NULL,
    channel           VARCHAR(255),
    title             TEXT,
    message_preview   VARCHAR(500),                  -- primeiros 500 chars para auditoria
    sent_at           TIMESTAMPTZ,
    success           BOOLEAN     NOT NULL DEFAULT FALSE,
    error_message     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notif_log_workload_time
    ON titlis_audit.notification_log (workload_id, created_at DESC);

CREATE INDEX idx_notif_log_namespace_time
    ON titlis_audit.notification_log (namespace_id, created_at DESC);

-- ================================================================
-- SCHEMA: titlis_ts — Time-series de Métricas
-- ================================================================

-- ----------------------------------------------------------------
-- resource_metrics  (CPU/memória coletados do Datadog)
-- Candidato a hypertable do TimescaleDB em produção.
-- ----------------------------------------------------------------
CREATE TABLE titlis_ts.resource_metrics (
    id                     BIGSERIAL   PRIMARY KEY,
    workload_id            UUID        NOT NULL,
    container_name         VARCHAR(255),
    metric_source          VARCHAR(50) NOT NULL DEFAULT 'datadog',
    cpu_avg_millicores     NUMERIC(10,3),
    cpu_p95_millicores     NUMERIC(10,3),
    mem_avg_mib            NUMERIC(10,3),
    mem_p95_mib            NUMERIC(10,3),
    -- Valores sugeridos conforme lógica _keep_max / suggest_*()
    suggested_cpu_request  VARCHAR(50),
    suggested_cpu_limit    VARCHAR(50),
    suggested_mem_request  VARCHAR(50),
    suggested_mem_limit    VARCHAR(50),
    sample_window          VARCHAR(20), -- '1h' | '24h' | '7d'
    collected_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_resource_metrics_workload_time
    ON titlis_ts.resource_metrics (workload_id, collected_at DESC);

-- ----------------------------------------------------------------
-- scorecard_scores  (série temporal plana para dashboards Grafana/Metabase)
-- Desnormalizado intencionalmente: elimina joins em tempo de query.
-- ----------------------------------------------------------------
CREATE TABLE titlis_ts.scorecard_scores (
    id                  BIGSERIAL   PRIMARY KEY,
    workload_id         UUID        NOT NULL,
    overall_score       NUMERIC(5,2) NOT NULL,
    resilience_score    NUMERIC(5,2),
    security_score      NUMERIC(5,2),
    cost_score          NUMERIC(5,2),
    performance_score   NUMERIC(5,2),
    operational_score   NUMERIC(5,2),
    compliance_score    NUMERIC(5,2),
    compliance_status   VARCHAR(50) NOT NULL,
    passed_rules        INTEGER,
    failed_rules        INTEGER,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_score_ts_workload_time
    ON titlis_ts.scorecard_scores (workload_id, recorded_at DESC);

CREATE INDEX idx_score_ts_time_compliance
    ON titlis_ts.scorecard_scores (recorded_at DESC, compliance_status);

-- ================================================================
-- VIEWS — abstração para Frontend e APIs
-- ================================================================

-- Dashboard principal: estado atual de todos os workloads
CREATE OR REPLACE VIEW titlis_oltp.v_workload_dashboard AS
SELECT
    w.id                    AS workload_id,
    c.name                  AS cluster_name,
    c.environment,
    n.name                  AS namespace,
    w.name                  AS workload_name,
    w.kind,
    w.service_tier,
    w.owner_team,
    sc.overall_score,
    sc.compliance_status,
    sc.passed_rules,
    sc.failed_rules,
    sc.critical_failures,
    sc.version              AS scorecard_version,
    sc.evaluated_at,
    ar.status               AS remediation_status,
    ar.github_pr_url,
    ar.github_pr_number,
    sc.updated_at           AS last_scored_at
FROM titlis_oltp.workloads w
JOIN titlis_oltp.namespaces n      ON n.id = w.namespace_id
JOIN titlis_oltp.clusters c        ON c.id = n.cluster_id
LEFT JOIN titlis_oltp.app_scorecards sc ON sc.workload_id = w.id
LEFT JOIN titlis_oltp.app_remediations ar ON ar.workload_id = w.id
WHERE w.is_active = TRUE
  AND n.is_excluded = FALSE;

-- Evolução de score com delta entre avaliações consecutivas
CREATE OR REPLACE VIEW titlis_audit.v_score_evolution AS
SELECT
    workload_id,
    scorecard_version,
    overall_score,
    compliance_status,
    passed_rules,
    failed_rules,
    evaluated_at,
    overall_score - LAG(overall_score) OVER (
        PARTITION BY workload_id ORDER BY evaluated_at
    ) AS score_delta
FROM titlis_audit.app_scorecard_history
ORDER BY workload_id, evaluated_at DESC;

-- Top regras que mais falham (análise de impacto)
CREATE OR REPLACE VIEW titlis_audit.v_top_failing_rules AS
SELECT
    (vr->>'rule_ref')   AS rule_id,
    (vr->>'pillar')     AS pillar,
    (vr->>'severity')   AS severity,
    COUNT(*)            AS total_failures,
    COUNT(DISTINCT h.workload_id) AS affected_workloads,
    MAX(h.evaluated_at) AS last_seen
FROM titlis_audit.app_scorecard_history h,
     jsonb_array_elements(h.validation_results) AS vr
WHERE (vr->>'passed')::BOOLEAN = FALSE
GROUP BY 1, 2, 3
ORDER BY total_failures DESC;

-- Eficácia das remediações: taxa de sucesso por workload
CREATE OR REPLACE VIEW titlis_audit.v_remediation_effectiveness AS
SELECT
    workload_id,
    COUNT(*)                                                       AS total_attempts,
    COUNT(*) FILTER (WHERE status = 'PR_MERGED')                   AS successful,
    COUNT(*) FILTER (WHERE status = 'FAILED')                      AS failed,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'PR_MERGED')
        / NULLIF(COUNT(*), 0), 2
    )                                                              AS success_rate_pct,
    MAX(triggered_at)                                              AS last_attempt_at
FROM titlis_audit.remediation_history
GROUP BY workload_id;

-- ================================================================
-- TRIGGERS — updated_at automático + audit trail
-- ================================================================

CREATE OR REPLACE FUNCTION titlis_oltp.fn_update_timestamp()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DO $$
DECLARE tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'clusters', 'namespaces', 'workloads', 'validation_rules',
        'app_scorecards', 'pillar_scores', 'app_remediations', 'slo_configs'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_update_ts
             BEFORE UPDATE ON titlis_oltp.%I
             FOR EACH ROW EXECUTE FUNCTION titlis_oltp.fn_update_timestamp()',
            tbl
        );
    END LOOP;
END;
$$;

-- Trigger: antes de sobrescrever scorecard atual, arquiva o estado anterior
CREATE OR REPLACE FUNCTION titlis_oltp.fn_scorecard_to_history()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- Só arquiva se a versão mudou (nova avaliação)
    IF OLD.version IS DISTINCT FROM NEW.version THEN
        INSERT INTO titlis_audit.app_scorecard_history (
            workload_id, scorecard_version, overall_score, compliance_status,
            total_rules, passed_rules, failed_rules, critical_failures,
            error_count, warning_count, pillar_scores, validation_results,
            evaluated_at, k8s_event_type
        )
        SELECT
            OLD.workload_id,
            OLD.version,
            OLD.overall_score,
            OLD.compliance_status::TEXT,
            OLD.total_rules, OLD.passed_rules, OLD.failed_rules,
            OLD.critical_failures, OLD.error_count, OLD.warning_count,
            COALESCE(
                (SELECT jsonb_agg(jsonb_build_object(
                    'pillar', pillar, 'score', score,
                    'passed_checks', passed_checks, 'failed_checks', failed_checks,
                    'weighted_score', weighted_score
                )) FROM titlis_oltp.pillar_scores WHERE scorecard_id = OLD.id),
                '[]'::jsonb
            ),
            COALESCE(
                (SELECT jsonb_agg(jsonb_build_object(
                    'rule_id', vr.id,
                    'rule_ref', r.rule_id,
                    'pillar', r.pillar,
                    'severity', r.severity,
                    'passed', vr.passed,
                    'message', vr.message,
                    'actual_value', vr.actual_value
                ))
                FROM titlis_oltp.validation_results vr
                JOIN titlis_oltp.validation_rules r ON r.id = vr.rule_id
                WHERE vr.scorecard_id = OLD.id),
                '[]'::jsonb
            ),
            OLD.evaluated_at,
            OLD.k8s_event_type;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_scorecard_to_history
BEFORE UPDATE ON titlis_oltp.app_scorecards
FOR EACH ROW
EXECUTE FUNCTION titlis_oltp.fn_scorecard_to_history();

-- Trigger: toda transição de status de remediação gera um registro histórico
CREATE OR REPLACE FUNCTION titlis_oltp.fn_remediation_to_history()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status OR OLD.version IS DISTINCT FROM NEW.version THEN
        INSERT INTO titlis_audit.remediation_history (
            workload_id, remediation_version, status, previous_status,
            scorecard_version, github_pr_number, github_pr_url, github_branch,
            repository_url, error_message, triggered_at, resolved_at
        )
        SELECT
            NEW.workload_id,
            NEW.version,
            NEW.status::TEXT,
            OLD.status::TEXT,
            (SELECT version FROM titlis_oltp.app_scorecards
             WHERE workload_id = NEW.workload_id),
            NEW.github_pr_number,
            NEW.github_pr_url,
            NEW.github_branch,
            NEW.repository_url,
            NEW.error_message,
            NEW.triggered_at,
            NEW.resolved_at;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_remediation_to_history
AFTER UPDATE ON titlis_oltp.app_remediations
FOR EACH ROW
EXECUTE FUNCTION titlis_oltp.fn_remediation_to_history();

-- ================================================================
-- INDEXES DE PERFORMANCE
-- ================================================================

-- OLTP — leituras do frontend
CREATE INDEX idx_workloads_namespace      ON titlis_oltp.workloads (namespace_id)
    WHERE is_active = TRUE;
CREATE INDEX idx_scorecard_compliance     ON titlis_oltp.app_scorecards (compliance_status);
CREATE INDEX idx_scorecard_score          ON titlis_oltp.app_scorecards (overall_score);
CREATE INDEX idx_remediation_status       ON titlis_oltp.app_remediations (status);
CREATE INDEX idx_val_results_rule_passed  ON titlis_oltp.validation_results (rule_id, passed);
CREATE INDEX idx_slo_datadog_id           ON titlis_oltp.slo_configs (datadog_slo_id);

-- Time-series — queries de dashboard
CREATE INDEX idx_ts_scores_recent
    ON titlis_ts.scorecard_scores (recorded_at DESC)
    WHERE recorded_at > now() - INTERVAL '90 days';

-- ================================================================
-- PARTICIONAMENTO (produção — recomendado após 6 meses de dados)
-- ================================================================
-- Converter titlis_audit.app_scorecard_history e titlis_ts.* para
-- PARTITION BY RANGE (evaluated_at / recorded_at) por trimestre.
-- Usar pg_partman para automação da criação de partições.
--
-- SELECT create_parent(
--     'titlis_audit.app_scorecard_history',
--     'evaluated_at', 'native', 'quarterly'
-- );
```

---

## Dicionário de Dados

### `titlis_oltp.clusters`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `cluster_id` | BIGINT IDENTITY | PK | Identificador único do cluster |
| `cluster_name` | VARCHAR(255) | UNIQUE | Nome do cluster Kubernetes |
| `environment` | VARCHAR(50) | Sim | `production`, `staging`, `develop` |
| `region` | VARCHAR(100) | Não | Região cloud (us-east-1, brazil-south...) |
| `provider` | VARCHAR(100) | Não | `aws`, `gcp`, `azure`, `on-prem` |
| `k8s_version` | VARCHAR(50) | Não | Versão do Kubernetes (ex: `1.29.0`) |
| `is_active` | BOOLEAN | Sim | Soft-delete do cluster |
| `created_at` | TIMESTAMPTZ | Sim | Criação do registro |
| `updated_at` | TIMESTAMPTZ | Sim | Última modificação; atualizada pela aplicação |

### `titlis_oltp.namespaces`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `namespace_id` | BIGINT IDENTITY | PK | Identificador único |
| `cluster_id` | BIGINT | FK | Cluster ao qual pertence |
| `namespace_name` | VARCHAR(255) | Sim | Nome do namespace no K8s |
| `is_excluded` | BOOLEAN | Sim | Reflete `excluded_namespaces` do `scorecard-config.yaml` |
| `labels` | JSONB | Não | Labels do namespace no K8s |
| `annotations` | JSONB | Não | Annotations do namespace no K8s |

### `titlis_oltp.workloads`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `workload_id` | BIGINT IDENTITY | PK | Identificador único |
| `namespace_id` | BIGINT | FK | Namespace ao qual pertence |
| `workload_name` | VARCHAR(255) | Sim | Nome do Deployment no K8s |
| `workload_kind` | VARCHAR(100) | Sim | `Deployment` (extensível para StatefulSet...) |
| `service_tier` | ENUM | Não | TIER_1 a TIER_4 — criticidade do serviço |
| `dd_git_repository_url` | VARCHAR(500) | Não | Pré-condição de auto-remediação; ausência bloqueia criação de PR |
| `backstage_component` | VARCHAR(255) | Não | Nome do componente no catálogo Backstage |
| `owner_team` | VARCHAR(255) | Não | Time responsável (via labels/Backstage) |
| `resource_version` | VARCHAR(100) | Não | K8s resourceVersion para detecção de mudanças |
| `is_active` | BOOLEAN | Sim | Soft-delete — workloads deletados não são removidos |

### `titlis_oltp.validation_rules`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `validation_rule_id` | BIGINT IDENTITY | PK | Identificador interno surrogate |
| `rule_id` | VARCHAR(50) | UNIQUE | Código legível: `RES-001`, `PERF-002` |
| `pillar` | ENUM | Sim | RESILIENCE, SECURITY, COST, PERFORMANCE, OPERATIONAL, COMPLIANCE |
| `rule_severity` | ENUM | Sim | CRITICAL > ERROR > WARNING > INFO > OPTIONAL |
| `rule_type` | ENUM | Sim | BOOLEAN, NUMERIC, ENUM, REGEX |
| `weight` | NUMERIC(5,2) | Sim | Peso na composição do pillar score |
| `rule_name` | VARCHAR(255) | Sim | Nome legível da regra para exibição |
| `is_remediable` | BOOLEAN | Sim | Se o operador pode gerar PR automaticamente |
| `remediation_category` | ENUM | Condicional | `resources` ou `hpa` (obrigatório se `is_remediable = true`) |
| `is_active` | BOOLEAN | Sim | Permite desativar regras sem deletar histórico |

### `titlis_oltp.app_scorecards`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `app_scorecard_id` | BIGINT IDENTITY | PK | Identificador único |
| `workload_id` | BIGINT | UNIQUE FK | Garante 1 scorecard atual por workload |
| `version` | INTEGER | Sim | Contador monotônico de avaliações; incremento sinaliza à aplicação para arquivar o estado anterior |
| `overall_score` | NUMERIC(5,2) | Sim | Score global 0–100 |
| `compliance_status` | ENUM | Sim | Estado de compliance atual |
| `total_rules` | INTEGER | Sim | Total de regras avaliadas |
| `passed_rules` | INTEGER | Sim | Regras aprovadas |
| `failed_rules` | INTEGER | Sim | Regras reprovadas |
| `critical_failures` | INTEGER | Sim | Regras CRITICAL que falharam — input primário para alertas |
| `error_count` | INTEGER | Sim | Regras ERROR que falharam |
| `warning_count` | INTEGER | Sim | Regras WARNING que falharam |
| `evaluated_at` | TIMESTAMPTZ | Sim | Timestamp da avaliação pelo operador |
| `k8s_event_type` | VARCHAR(50) | Não | `resume` / `create` / `update` — contexto do evento que disparou |
| `raw_metadata` | JSONB | Não | Labels, annotations e campos adicionais do body K8s |

### `titlis_oltp.pillar_scores`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `pillar_score_id` | BIGINT IDENTITY | PK | Identificador único |
| `app_scorecard_id` | BIGINT | FK | Scorecard ao qual pertence |
| `pillar` | ENUM | Sim | Um dos 6 pilares de maturidade |
| `pillar_score` | NUMERIC(5,2) | Sim | Score do pilar (0–100) |
| `passed_checks` | INTEGER | Sim | Regras aprovadas neste pilar |
| `failed_checks` | INTEGER | Sim | Regras reprovadas neste pilar |
| `weighted_score` | NUMERIC(8,4) | Não | Score ponderado pelos pesos das regras |

### `titlis_oltp.validation_results`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `validation_result_id` | BIGINT IDENTITY | PK | Identificador único |
| `app_scorecard_id` | BIGINT | FK | Scorecard avaliado |
| `validation_rule_id` | BIGINT | FK | Regra avaliada |
| `rule_passed` | BOOLEAN | Sim | Se a regra passou |
| `result_message` | TEXT | Não | Mensagem descritiva do resultado |
| `actual_value` | TEXT | Não | Valor observado no Deployment (ex: `"100m"` para CPU) |

### `titlis_oltp.app_remediations`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `app_remediation_id` | BIGINT IDENTITY | PK | Identificador único |
| `workload_id` | BIGINT | UNIQUE FK | 1 remediação ativa por workload — espelha `_pending` set em memória |
| `version` | INTEGER | Sim | Contador de ciclos de remediação |
| `app_remediation_status` | ENUM | Sim | Máquina de estados: `PENDING → IN_PROGRESS → PR_OPEN → PR_MERGED/FAILED` |
| `app_scorecard_id` | BIGINT | FK | Scorecard que originou esta remediação |
| `github_pr_number` | INTEGER | Não | Número do PR para deep-link |
| `github_pr_url` | VARCHAR(500) | Não | URL completa do PR |
| `github_branch` | VARCHAR(255) | Não | Branch criado: `fix/auto-remediation-{namespace}-{resource}-*` |
| `repository_url` | VARCHAR(500) | Não | Repositório alvo extraído de `DD_GIT_REPOSITORY_URL` |
| `error_message` | TEXT | Não | Erro em caso de falha |
| `triggered_at` | TIMESTAMPTZ | Sim | Quando a remediação foi iniciada |
| `resolved_at` | TIMESTAMPTZ | Não | Preenchido quando status terminal (PR_MERGED ou FAILED) |

### `titlis_oltp.remediation_issues`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `remediation_issue_id` | BIGINT IDENTITY | PK | Identificador único |
| `app_remediation_id` | BIGINT | FK | Remediação à qual pertence |
| `validation_rule_id` | BIGINT | FK | Regra que gerou esta issue |
| `issue_category` | ENUM | Sim | `resources` (CPU/mem) ou `hpa` |
| `suggested_value` | VARCHAR(100) | Não | Valor calculado pelo operador antes de `_keep_max` |
| `applied_value` | VARCHAR(100) | Não | Valor efetivamente escrito no PR (após `_keep_max`) |

### `titlis_oltp.slo_configs`

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `slo_config_id` | BIGINT IDENTITY | PK | Identificador único |
| `namespace_id` | BIGINT | FK | Namespace ao qual o SLO pertence |
| `slo_config_name` | VARCHAR(255) | Sim | Nome do CRD SLOConfig |
| `slo_type` | ENUM | Sim | METRIC, MONITOR ou TIME_SLICE |
| `timeframe` | ENUM | Sim | `7d`, `30d` ou `90d` |
| `target` | NUMERIC(6,4) | Sim | Target de compliance (0–100) |
| `warning` | NUMERIC(6,4) | Não | Threshold de warning — deve ser menor que `target` |
| `datadog_slo_id` | VARCHAR(255) | Não | ID do SLO criado no Datadog |
| `datadog_slo_state` | ENUM | Não | Estado atual: `ok`, `warning`, `error`, `no_data` |
| `last_sync_at` | TIMESTAMPTZ | Não | Última sincronização com o Datadog |
| `spec_raw` | JSONB | Não | Spec completo do CRD para auditoria e replay |

### `titlis_audit.app_scorecard_history`

| Coluna | Tipo | Descrição |
|---|---|---|
| `app_scorecard_history_id` | BIGINT IDENTITY | Chave primária surrogate |
| `workload_id` | BIGINT | Ref lógica sem FK — histórico sobrevive à deleção do workload |
| `scorecard_version` | INTEGER | Versão arquivada (version anterior ao incremento pela aplicação) |
| `pillar_scores` | JSONB | Snapshot desnormalizado: `[{pillar, score, passed_checks, failed_checks, weighted_score}]` |
| `validation_results` | JSONB | Snapshot completo: `[{rule_ref, pillar, severity, passed, message, actual_value}]` |
| `evaluated_at` | TIMESTAMPTZ | Timestamp da avaliação original (não do arquivamento) |

### `titlis_audit.remediation_history`

| Coluna | Tipo | Descrição |
|---|---|---|
| `remediation_history_id` | BIGINT IDENTITY | Chave primária surrogate |
| `workload_id` | BIGINT | Ref lógica sem FK |
| `remediation_version` | INTEGER | Versão da remediação no momento do registro |
| `app_remediation_status` | VARCHAR(50) | Status após a transição |
| `previous_app_remediation_status` | VARCHAR(50) | Status antes da transição — permite reconstruir máquina de estados |
| `issues_snapshot` | JSONB | Snapshot das issues no momento da transição |

### `titlis_ts.resource_metrics`

| Coluna | Tipo | Descrição |
|---|---|---|
| `cpu_avg_millicores` | NUMERIC(10,3) | Média de CPU coletada do Datadog (input para `suggest_cpu_request`) |
| `cpu_p95_millicores` | NUMERIC(10,3) | Percentil 95 de CPU (input para `suggest_cpu_limit`) |
| `mem_avg_mib` | NUMERIC(10,3) | Média de memória em MiB (input para `suggest_memory_request`) |
| `mem_p95_mib` | NUMERIC(10,3) | Percentil 95 de memória (input para `suggest_memory_limit`) |
| `suggested_cpu_request` | VARCHAR(50) | Valor sugerido após lógica `_keep_max` |
| `suggested_cpu_limit` | VARCHAR(50) | Valor sugerido após lógica `_keep_max` |
| `suggested_mem_request` | VARCHAR(50) | Valor sugerido após lógica `_keep_max` |
| `suggested_mem_limit` | VARCHAR(50) | Valor sugerido após lógica `_keep_max` |
| `sample_window` | VARCHAR(20) | Janela de coleta: `1h`, `24h`, `7d` |
| `collected_at` | TIMESTAMPTZ | Momento da coleta |

### `titlis_ts.scorecard_scores`

| Coluna | Tipo | Descrição |
|---|---|---|
| `workload_id` | UUID | Workload avaliado |
| `overall_score` | NUMERIC(5,2) | Score global no momento do registro |
| `resilience_score` | NUMERIC(5,2) | Score do pilar RESILIENCE |
| `security_score` | NUMERIC(5,2) | Score do pilar SECURITY |
| `cost_score` | NUMERIC(5,2) | Score do pilar COST |
| `performance_score` | NUMERIC(5,2) | Score do pilar PERFORMANCE |
| `operational_score` | NUMERIC(5,2) | Score do pilar OPERATIONAL |
| `compliance_score` | NUMERIC(5,2) | Score do pilar COMPLIANCE |
| `compliance_status` | VARCHAR(50) | Status de compliance no momento |
| `recorded_at` | TIMESTAMPTZ | Timestamp de inserção — eixo X de todos os gráficos de evolução |

---

## Justificativa Técnica da Arquitetura

### Por que SCD Type 4 e não Type 2?

**SCD Type 2** adiciona `valid_from`, `valid_to` e `is_current` na mesma tabela. Para o caso do Titlis:

```sql
-- Type 2 — toda query precisa de filtro:
SELECT * FROM app_scorecards WHERE workload_id = $1 AND is_current = TRUE;

-- Type 4 — zero overhead na leitura do estado atual:
SELECT * FROM app_scorecards WHERE workload_id = $1;
```

Com centenas de Deployments sendo reavaliados a cada `RECONCILE_INTERVAL_SECONDS=300`, o índice `UNIQUE(workload_id)` em `app_scorecards` garante que o planner sempre use **Index Scan** com cardinalidade = 1. Type 2 obrigaria um filtro adicional em uma tabela crescente.

**Type 4** mantém o benefício analítico completo (histórico ilimitado em `app_scorecard_history`) sem penalizar o caminho quente OLTP.

### Por que Snapshot JSONB no histórico?

Os snapshots `pillar_scores JSONB` e `validation_results JSONB` na tabela de histórico eliminam joins na camada analítica:

```sql
-- Com joins normalizados (N queries ou joins complexos):
SELECT h.*, ps.score, vr.passed
FROM app_scorecard_history h
JOIN pillar_score_history ps ON ...
JOIN validation_result_history vr ON ...
WHERE h.workload_id = $1;

-- Com JSONB snapshot (1 query, GIN index):
SELECT evaluated_at,
       overall_score,
       pillar_scores->>'score' AS resilience_score
FROM app_scorecard_history
WHERE workload_id = $1
ORDER BY evaluated_at DESC;
```

O custo é espaço em disco (aceitável — histórico cresce ~2 KB por avaliação) e impossibilidade de fazer JOIN com `validation_rules` no histórico (mitigado incluindo `rule_ref` como string dentro do JSONB).

### Por que três schemas distintos?

```
titlis_oltp  → SLA de latência:   < 5ms    (índices B-tree, rows pequenas)
titlis_audit → SLA de completude: 100%     (append-only + GIN)
titlis_ts    → SLA de volume:     milhões  (candidato a hypertable)
```

Separar schemas permite políticas de acesso distintas:

```sql
GRANT SELECT ON ALL TABLES IN SCHEMA titlis_audit TO analytics_role;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA titlis_oltp TO operator_role;
GRANT INSERT ON ALL TABLES IN SCHEMA titlis_ts TO metrics_collector_role;
```

Facilita migração futura de `titlis_ts` para TimescaleDB ou InfluxDB sem tocar no schema OLTP.

### Trigger como "wall" entre OLTP e Audit

O trigger `trg_scorecard_to_history` é acionado **antes** do UPDATE (`BEFORE`), garantindo que o estado anterior nunca seja perdido mesmo em caso de falha na transação do novo estado. A condição `OLD.version IS DISTINCT FROM NEW.version` evita arquivamentos desnecessários em updates de `updated_at` sem nova avaliação.

O trigger de remediação usa `AFTER UPDATE` com verificação de `previous_status` para registrar cada transição da máquina de estados:

```
PENDING → IN_PROGRESS → PR_OPEN → PR_MERGED
                                ↘ FAILED
                                ↘ SKIPPED
```

### Particionamento como próximo passo

As tabelas `titlis_audit.*` e `titlis_ts.*` são projetadas para particionamento declarativo por `RANGE(evaluated_at)`. Com `pg_partman`, partições trimestrais são criadas automaticamente e partições antigas podem ser arquivadas em tablespaces mais baratos (cold storage) ou exportadas para Parquet sem mudança no DDL das aplicações.

---

## Diagrama ER Simplificado

```
titlis_oltp
───────────
clusters ──< namespaces ──< workloads ──────────────────────────────┐
                                 │                                  │
                                 ├──< app_scorecards ──< pillar_scores
                                 │          │
                                 │          └──< validation_results
                                 │                      │
                                 │                      └── validation_rules (catálogo)
                                 │
                                 └──< app_remediations
                                           └──< remediation_issues

                    ┌──────────────────────────────────────────┐
                    │  trigger BEFORE UPDATE (version changed)  │
                    └───────────────────┬──────────────────────┘
                                        ▼
titlis_audit
────────────
app_scorecard_history    (JSONB snapshots de pillar_scores + validation_results)
pillar_score_history     (granularidade por pilar para gráficos de evolução)
remediation_history      (log de transições: status + previous_status)
slo_compliance_history   (sync actions + actual_value do Datadog)
notification_log         (auditoria de mensagens Slack)

titlis_ts
─────────
scorecard_scores         (série temporal plana — Grafana/Metabase)
resource_metrics         (CPU/mem do Datadog — candidato a TimescaleDB)
```

---

## Views de Referência Rápida

| View | Schema | Propósito |
|---|---|---|
| `v_workload_dashboard` | `titlis_oltp` | Estado atual de todos os workloads — input principal do Frontend |
| `v_score_evolution` | `titlis_audit` | Histórico de scores com `score_delta` por avaliação consecutiva |
| `v_top_failing_rules` | `titlis_audit` | Ranking de regras que mais falham — input para priorização de melhorias |
| `v_remediation_effectiveness` | `titlis_audit` | Taxa de sucesso de remediações por workload |

---

## Schema Evolution — Fases Futuras

> PostgreSQL 15+ | Estratégia: SCD Type 4 + Multi-Tenant
> Executar em ordem: FASE 1 → FASE 2 → ... → FASE 7
> Cada fase deve ter rollback script associado. Testar em staging antes de produção.

```sql
-- ================================================================
-- TITLIS OPERATOR — EVOLUÇÃO DO SCHEMA PARA FUTURAS FUNCIONALIDADES
-- PostgreSQL 15+ | Estratégia: SCD Type 4 + Multi-Tenant
-- ================================================================

-- ================================================================
-- FASE 1: MULTI-TENANT FOUNDATION
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.tenants (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL UNIQUE,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    plan            VARCHAR(50) NOT NULL DEFAULT 'free',  -- free, pro, enterprise
    max_clusters    INTEGER     NOT NULL DEFAULT 5,
    max_workloads   INTEGER     NOT NULL DEFAULT 100,
    max_products    INTEGER     NOT NULL DEFAULT 10,
    features        JSONB       NOT NULL DEFAULT '{}',
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Adicionar tenant_id a todas as tabelas OLTP (migration)
ALTER TABLE titlis_oltp.clusters    ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT gen_random_uuid() REFERENCES titlis_oltp.tenants(id);
ALTER TABLE titlis_oltp.namespaces  ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT gen_random_uuid() REFERENCES titlis_oltp.tenants(id);
ALTER TABLE titlis_oltp.workloads   ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT gen_random_uuid() REFERENCES titlis_oltp.tenants(id);
ALTER TABLE titlis_oltp.app_scorecards   ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT gen_random_uuid() REFERENCES titlis_oltp.tenants(id);
ALTER TABLE titlis_oltp.app_remediations ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT gen_random_uuid() REFERENCES titlis_oltp.tenants(id);
ALTER TABLE titlis_oltp.slo_configs ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT gen_random_uuid() REFERENCES titlis_oltp.tenants(id);

CREATE INDEX IF NOT EXISTS idx_clusters_tenant    ON titlis_oltp.clusters (tenant_id);
CREATE INDEX IF NOT EXISTS idx_namespaces_tenant  ON titlis_oltp.namespaces (tenant_id);
CREATE INDEX IF NOT EXISTS idx_workloads_tenant   ON titlis_oltp.workloads (tenant_id);
CREATE INDEX IF NOT EXISTS idx_scorecards_tenant  ON titlis_oltp.app_scorecards (tenant_id);
CREATE INDEX IF NOT EXISTS idx_remediations_tenant ON titlis_oltp.app_remediations (tenant_id);

-- Row Level Security
ALTER TABLE titlis_oltp.clusters         ENABLE ROW LEVEL SECURITY;
ALTER TABLE titlis_oltp.namespaces       ENABLE ROW LEVEL SECURITY;
ALTER TABLE titlis_oltp.workloads        ENABLE ROW LEVEL SECURITY;
ALTER TABLE titlis_oltp.app_scorecards   ENABLE ROW LEVEL SECURITY;
ALTER TABLE titlis_oltp.app_remediations ENABLE ROW LEVEL SECURITY;
ALTER TABLE titlis_oltp.slo_configs      ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_policy ON titlis_oltp.clusters         USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID);
CREATE POLICY tenant_isolation_policy ON titlis_oltp.namespaces       USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID);
CREATE POLICY tenant_isolation_policy ON titlis_oltp.workloads        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID);
CREATE POLICY tenant_isolation_policy ON titlis_oltp.app_scorecards   USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID);
CREATE POLICY tenant_isolation_policy ON titlis_oltp.app_remediations USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID);
CREATE POLICY tenant_isolation_policy ON titlis_oltp.slo_configs      USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID);

-- Trigger para setar tenant_id automaticamente
CREATE OR REPLACE FUNCTION titlis_oltp.fn_set_tenant_id()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS NULL THEN
        NEW.tenant_id = current_setting('app.current_tenant_id', TRUE)::UUID;
    END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'clusters', 'namespaces', 'workloads', 'app_scorecards',
        'app_remediations', 'slo_configs', 'business_products',
        'technical_tickets', 'ai_suggestions'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_set_tenant_id
             BEFORE INSERT ON titlis_oltp.%I
             FOR EACH ROW EXECUTE FUNCTION titlis_oltp.fn_set_tenant_id()',
            tbl
        );
    END LOOP;
END;
$$;

-- ================================================================
-- FASE 2: MULTI-VCS SUPPORT
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.vcs_providers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    provider_type   VARCHAR(50) NOT NULL,  -- github, gitlab, bitbucket
    name            VARCHAR(255) NOT NULL,
    base_url        TEXT,
    is_default      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider_type, name)
);

CREATE TABLE IF NOT EXISTS titlis_oltp.vcs_tokens (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id     UUID        NOT NULL REFERENCES titlis_oltp.vcs_providers(id),
    token_hash      VARCHAR(255) NOT NULL,  -- hash do token, nunca plain text
    scopes          TEXT[],
    expires_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    priority        INTEGER     NOT NULL DEFAULT 0,  -- ordem de failover
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vcs_tokens_provider ON titlis_oltp.vcs_tokens (provider_id, priority);

ALTER TABLE titlis_oltp.app_remediations ADD COLUMN IF NOT EXISTS vcs_provider_id UUID REFERENCES titlis_oltp.vcs_providers(id);
ALTER TABLE titlis_oltp.app_remediations ADD COLUMN IF NOT EXISTS vcs_type VARCHAR(50);

-- ================================================================
-- FASE 3: OBSERVABILITY HUB
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.observability_providers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    provider_type   VARCHAR(50) NOT NULL,  -- datadog, dynatrace, prometheus, newrelic
    name            VARCHAR(255) NOT NULL,
    base_url        TEXT,
    api_key_secret  VARCHAR(255),
    is_default      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    config          JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider_type, name)
);

CREATE TABLE IF NOT EXISTS titlis_oltp.slo_provider_configs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slo_config_id   UUID        NOT NULL REFERENCES titlis_oltp.slo_configs(id),
    provider_id     UUID        NOT NULL REFERENCES titlis_oltp.observability_providers(id),
    external_slo_id VARCHAR(255),
    sync_status     VARCHAR(50) NOT NULL DEFAULT 'pending',
    last_sync_at    TIMESTAMPTZ,
    sync_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (slo_config_id, provider_id)
);

-- ================================================================
-- FASE 4: FINOPS INTEGRATION
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.cost_providers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    provider_type   VARCHAR(50) NOT NULL,  -- aws, gcp, azure, castai
    name            VARCHAR(255) NOT NULL,
    account_id      VARCHAR(255),
    is_default      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    config          JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider_type, name)
);

CREATE TABLE IF NOT EXISTS titlis_oltp.workload_costs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id     UUID        NOT NULL REFERENCES titlis_oltp.workloads(id),
    provider_id     UUID        NOT NULL REFERENCES titlis_oltp.cost_providers(id),
    cost_usd        NUMERIC(12,2) NOT NULL,
    period_start    DATE        NOT NULL,
    period_end      DATE        NOT NULL,
    currency        VARCHAR(3)  NOT NULL DEFAULT 'USD',
    breakdown       JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workload_id, provider_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_workload_costs_period ON titlis_oltp.workload_costs (period_start, period_end);

CREATE TABLE IF NOT EXISTS titlis_oltp.cost_scorecard_details (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scorecard_id     UUID        NOT NULL REFERENCES titlis_oltp.app_scorecards(id),
    cost_per_request NUMERIC(12,6),
    cost_per_month   NUMERIC(12,2),
    efficiency_score NUMERIC(5,2) CHECK (efficiency_score BETWEEN 0 AND 100),
    waste_identified NUMERIC(12,2),
    recommendations  JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scorecard_id)
);

-- ================================================================
-- FASE 5: BUSINESS PRODUCTS
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.business_products (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    owner_team_id   UUID,
    owner_team_name VARCHAR(255),
    health_score    NUMERIC(5,2) CHECK (health_score BETWEEN 0 AND 100),
    risk_level      VARCHAR(50),  -- LOW, MEDIUM, HIGH, CRITICAL
    cost_monthly    NUMERIC(12,2),
    slo_compliance  NUMERIC(5,2),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_products_tenant ON titlis_oltp.business_products (tenant_id);
CREATE INDEX IF NOT EXISTS idx_products_health ON titlis_oltp.business_products (health_score DESC);

CREATE TABLE IF NOT EXISTS titlis_oltp.product_applications (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id       UUID        NOT NULL REFERENCES titlis_oltp.business_products(id) ON DELETE CASCADE,
    workload_id      UUID        NOT NULL REFERENCES titlis_oltp.workloads(id),
    weight           NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    discovery_source VARCHAR(50) NOT NULL,  -- tags, manifest, labels, backstage, manual
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, workload_id)
);

CREATE INDEX IF NOT EXISTS idx_product_apps_product  ON titlis_oltp.product_applications (product_id);
CREATE INDEX IF NOT EXISTS idx_product_apps_workload ON titlis_oltp.product_applications (workload_id);

CREATE TABLE IF NOT EXISTS titlis_audit.product_health_history (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id        UUID        NOT NULL,
    health_score      NUMERIC(5,2) NOT NULL,
    risk_level        VARCHAR(50),
    cost_monthly      NUMERIC(12,2),
    slo_compliance    NUMERIC(5,2),
    application_count INTEGER     NOT NULL,
    snapshot          JSONB       NOT NULL,
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_health_product_time
    ON titlis_audit.product_health_history (product_id, recorded_at DESC);

-- Views de produto
CREATE OR REPLACE VIEW titlis_oltp.v_tenant_dashboard AS
SELECT
    t.id                    AS tenant_id,
    t.name                  AS tenant_name,
    t.slug,
    t.plan,
    COUNT(DISTINCT c.id)    AS cluster_count,
    COUNT(DISTINCT w.id)    AS workload_count,
    COUNT(DISTINCT p.id)    AS product_count,
    AVG(sc.overall_score)   AS avg_health_score,
    SUM(wc.cost_usd)        AS total_monthly_cost,
    t.is_active,
    t.created_at
FROM titlis_oltp.tenants t
LEFT JOIN titlis_oltp.clusters c ON c.tenant_id = t.id AND c.is_active = TRUE
LEFT JOIN titlis_oltp.namespaces n ON n.tenant_id = t.id AND n.is_excluded = FALSE
LEFT JOIN titlis_oltp.workloads w ON w.tenant_id = t.id AND w.is_active = TRUE
LEFT JOIN titlis_oltp.app_scorecards sc ON sc.workload_id = w.id
LEFT JOIN titlis_oltp.business_products p ON p.tenant_id = t.id AND p.is_active = TRUE
LEFT JOIN titlis_oltp.workload_costs wc ON wc.workload_id = w.id
GROUP BY t.id;

CREATE OR REPLACE VIEW titlis_oltp.v_product_health_dashboard AS
SELECT
    p.id                    AS product_id,
    p.name                  AS product_name,
    p.owner_team_name,
    p.health_score,
    p.risk_level,
    p.cost_monthly,
    p.slo_compliance,
    COUNT(pa.workload_id)     AS application_count,
    AVG(sc.overall_score)     AS avg_application_score,
    MIN(sc.overall_score)     AS min_application_score,
    MAX(sc.critical_failures) AS max_critical_failures,
    p.updated_at              AS last_updated
FROM titlis_oltp.business_products p
LEFT JOIN titlis_oltp.product_applications pa ON pa.product_id = p.id
LEFT JOIN titlis_oltp.app_scorecards sc ON sc.workload_id = pa.workload_id
WHERE p.is_active = TRUE
GROUP BY p.id;

-- ================================================================
-- FASE 6: AI AGENTS
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.ai_agent_configs (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    agent_type   VARCHAR(50) NOT NULL,  -- debt_identification, remediation_suggestion, risk_prediction
    llm_provider VARCHAR(50) NOT NULL,  -- openai, anthropic, gemini, local
    is_enabled   BOOLEAN     NOT NULL DEFAULT FALSE,
    config       JSONB       NOT NULL DEFAULT '{}',
    safety_rules JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, agent_type)
);

CREATE TABLE IF NOT EXISTS titlis_audit.ai_agent_executions (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID        NOT NULL,
    agent_type            VARCHAR(50) NOT NULL,
    workload_id           UUID,
    input_snapshot        JSONB       NOT NULL,
    output_snapshot       JSONB       NOT NULL,
    safety_check_passed   BOOLEAN     NOT NULL,
    human_review_required BOOLEAN     NOT NULL DEFAULT TRUE,
    execution_time_ms     INTEGER,
    token_usage           JSONB,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_executions_tenant_time
    ON titlis_audit.ai_agent_executions (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS titlis_oltp.ai_suggestions (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id      UUID        NOT NULL REFERENCES titlis_oltp.workloads(id),
    agent_type       VARCHAR(50) NOT NULL,
    suggestion_type  VARCHAR(50) NOT NULL,  -- code_change, config_change, documentation
    description      TEXT        NOT NULL,
    confidence_score NUMERIC(5,2) CHECK (confidence_score BETWEEN 0 AND 100),
    status           VARCHAR(50) NOT NULL DEFAULT 'pending',
    pr_number        INTEGER,
    pr_url           TEXT,
    reviewed_by      VARCHAR(255),
    reviewed_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_suggestions_workload ON titlis_oltp.ai_suggestions (workload_id);
CREATE INDEX IF NOT EXISTS idx_ai_suggestions_status   ON titlis_oltp.ai_suggestions (status);

-- ================================================================
-- FASE 7: PROJECT MANAGEMENT INTEGRATION
-- ================================================================

CREATE TABLE IF NOT EXISTS titlis_oltp.pm_providers (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    provider_type  VARCHAR(50) NOT NULL,  -- jira, linear, azure_devops, clickup
    name           VARCHAR(255) NOT NULL,
    base_url       TEXT,
    api_key_secret VARCHAR(255),
    is_default     BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    config         JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider_type, name)
);

CREATE TABLE IF NOT EXISTS titlis_oltp.technical_tickets (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    workload_id         UUID        REFERENCES titlis_oltp.workloads(id),
    product_id          UUID        REFERENCES titlis_oltp.business_products(id),
    provider_id         UUID        NOT NULL REFERENCES titlis_oltp.pm_providers(id),
    external_ticket_id  VARCHAR(255),
    external_ticket_url TEXT,
    title               VARCHAR(500) NOT NULL,
    description         TEXT,
    priority            VARCHAR(50),
    status              VARCHAR(50),
    source_rule_id      VARCHAR(50),
    estimated_impact    JSONB,
    auto_created        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tech_tickets_tenant   ON titlis_oltp.technical_tickets (tenant_id);
CREATE INDEX IF NOT EXISTS idx_tech_tickets_workload ON titlis_oltp.technical_tickets (workload_id);
CREATE INDEX IF NOT EXISTS idx_tech_tickets_status   ON titlis_oltp.technical_tickets (status);

CREATE TABLE IF NOT EXISTS titlis_oltp.technical_okrs (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID        NOT NULL REFERENCES titlis_oltp.tenants(id),
    product_id   UUID        REFERENCES titlis_oltp.business_products(id),
    objective    TEXT        NOT NULL,
    period_start DATE        NOT NULL,
    period_end   DATE        NOT NULL,
    status       VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS titlis_oltp.technical_okr_key_results (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    okr_id           UUID        NOT NULL REFERENCES titlis_oltp.technical_okrs(id) ON DELETE CASCADE,
    description      TEXT        NOT NULL,
    current_value    NUMERIC(12,2),
    target_value     NUMERIC(12,2) NOT NULL,
    unit             VARCHAR(50),
    progress_percent NUMERIC(5,2) CHECK (progress_percent BETWEEN 0 AND 100),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ================================================================
-- PARTITIONING STRATEGY — Produção (após 6 meses de dados)
-- NOTA: Requer extensão pg_partman
-- ================================================================

-- SELECT pg_partman.create_parent('titlis_audit.app_scorecard_history', 'evaluated_at', 'native', 'quarterly');
-- SELECT pg_partman.create_parent('titlis_audit.remediation_history',   'created_at',   'native', 'quarterly');
-- SELECT pg_partman.create_parent('titlis_ts.scorecard_scores',         'recorded_at',  'native', 'monthly');
-- SELECT pg_partman.create_parent('titlis_ts.resource_metrics',         'collected_at', 'native', 'weekly');

-- ================================================================
-- END OF SCHEMA EVOLUTION
-- ================================================================
```

### Tabelas por Fase

| Fase | Tabelas Novas | Alterações em Existentes |
|------|--------------|--------------------------|
| 1 — Multi-Tenant | `tenants` | `tenant_id` + RLS + trigger em todas as tabelas OLTP |
| 2 — Multi-VCS | `vcs_providers`, `vcs_tokens` | `vcs_provider_id`, `vcs_type` em `app_remediations` |
| 3 — Observability Hub | `observability_providers`, `slo_provider_configs` | — |
| 4 — FinOps | `cost_providers`, `workload_costs`, `cost_scorecard_details` | — |
| 5 — Business Products | `business_products`, `product_applications`, `product_health_history` | Views `v_tenant_dashboard`, `v_product_health_dashboard` |
| 6 — AI Agents | `ai_agent_configs`, `ai_agent_executions`, `ai_suggestions` | — |
| 7 — Project Management | `pm_providers`, `technical_tickets`, `technical_okrs`, `technical_okr_key_results` | — |
