-- ================================================================
-- TITLIS OPERATOR — DATABASE SCHEMA
-- PostgreSQL 15+ | Estratégia: SCD Type 4 + Append-only Time-series
-- ================================================================
-- Status: criado / não integrado ao operador
-- Gerado a partir de: docs/modelagem-dados.md
--
-- Compatível com o estado atual do operador (branch feat/slo-inteligent).
-- tenant_id nullable em todas as tabelas — permite operação single-tenant
-- hoje e habilita RLS multi-tenant na Fase 1 sem migração de schema.
--
-- Padrões adotados (revisão DBA):
--   - PKs no formato <nome_da_tabela>_id com tipo BIGINT GENERATED ALWAYS AS IDENTITY
--   - Nomes de colunas compostos: evita conflito com palavras reservadas (name, type, status)
--   - VARCHAR(n) quando tamanho máximo é conhecido; TEXT apenas quando tamanho é indefinido
--   - COMMENT ON TABLE/COLUMN em todas as tabelas e colunas
--   - Regras de atualização (updated_at, audit trail) gerenciadas pela aplicação — sem triggers DML
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

-- SLOAppFramework: espelha domain/models.py SLOAppFramework enum
-- Usado por SLOService.auto_detect_framework e SLOController
CREATE TYPE titlis_oltp.slo_app_framework AS ENUM (
    'WSGI', 'FASTAPI', 'AIOHTTP'
);

-- ================================================================
-- SCHEMA: titlis_oltp — Estado Atual (OLTP)
-- ================================================================

-- ----------------------------------------------------------------
-- tenants (Fase 1 — multi-tenant foundation)
-- Nullable nos relacionamentos enquanto operador é single-tenant.
-- Na Fase 1, adicionar NOT NULL + RLS policies via migration.
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.tenants (
    tenant_id   BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_name VARCHAR(255) NOT NULL UNIQUE,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    tenant_plan VARCHAR(50)  NOT NULL DEFAULT 'free',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_oltp.tenants             IS 'Organizações ou times que utilizam o Titlis Operator (base multi-tenant para Fase 1)';
COMMENT ON COLUMN titlis_oltp.tenants.tenant_id   IS 'Chave primária surrogate, gerada automaticamente';
COMMENT ON COLUMN titlis_oltp.tenants.tenant_name IS 'Nome de exibição da organização';
COMMENT ON COLUMN titlis_oltp.tenants.slug        IS 'Identificador URL-safe único da organização';
COMMENT ON COLUMN titlis_oltp.tenants.is_active   IS 'Soft-delete do tenant';
COMMENT ON COLUMN titlis_oltp.tenants.tenant_plan IS 'Plano contratado: free | pro | enterprise';
COMMENT ON COLUMN titlis_oltp.tenants.created_at  IS 'Data de criação do registro';
COMMENT ON COLUMN titlis_oltp.tenants.updated_at  IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

-- ----------------------------------------------------------------
-- clusters
-- tenant_id nullable: suporta single-tenant hoje, multi-tenant na Fase 1.
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.clusters (
    cluster_id   BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id    BIGINT       REFERENCES titlis_oltp.tenants(tenant_id),
    cluster_name VARCHAR(255) NOT NULL UNIQUE,
    environment  VARCHAR(50)  NOT NULL,
    region       VARCHAR(100),
    provider     VARCHAR(100),
    k8s_version  VARCHAR(50),
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_oltp.clusters              IS 'Clusters Kubernetes monitorados pelo operador';
COMMENT ON COLUMN titlis_oltp.clusters.cluster_id   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.clusters.tenant_id    IS 'Referência ao tenant proprietário; nullable para suporte single-tenant';
COMMENT ON COLUMN titlis_oltp.clusters.cluster_name IS 'Nome único do cluster Kubernetes';
COMMENT ON COLUMN titlis_oltp.clusters.environment  IS 'Ambiente do cluster: production, staging, develop';
COMMENT ON COLUMN titlis_oltp.clusters.region       IS 'Região cloud (us-east-1, brazil-south, etc.)';
COMMENT ON COLUMN titlis_oltp.clusters.provider     IS 'Provedor de nuvem: aws, gcp, azure, on-prem';
COMMENT ON COLUMN titlis_oltp.clusters.k8s_version  IS 'Versão do Kubernetes (ex: 1.29.0)';
COMMENT ON COLUMN titlis_oltp.clusters.is_active    IS 'Soft-delete do cluster';
COMMENT ON COLUMN titlis_oltp.clusters.updated_at   IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

CREATE INDEX idx_clusters_tenant ON titlis_oltp.clusters (tenant_id) WHERE tenant_id IS NOT NULL;

-- ----------------------------------------------------------------
-- namespaces
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.namespaces (
    namespace_id   BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cluster_id     BIGINT       NOT NULL REFERENCES titlis_oltp.clusters(cluster_id),
    namespace_name VARCHAR(255) NOT NULL,
    is_excluded    BOOLEAN      NOT NULL DEFAULT FALSE,
    labels         JSONB,
    annotations    JSONB,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (cluster_id, namespace_name)
);

COMMENT ON TABLE  titlis_oltp.namespaces                IS 'Namespaces Kubernetes por cluster';
COMMENT ON COLUMN titlis_oltp.namespaces.namespace_id   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.namespaces.cluster_id     IS 'Cluster ao qual o namespace pertence';
COMMENT ON COLUMN titlis_oltp.namespaces.namespace_name IS 'Nome do namespace no Kubernetes';
COMMENT ON COLUMN titlis_oltp.namespaces.is_excluded    IS 'Reflete excluded_namespaces do scorecard-config.yaml; namespaces excluídos não são avaliados';
COMMENT ON COLUMN titlis_oltp.namespaces.labels         IS 'Labels do namespace no Kubernetes';
COMMENT ON COLUMN titlis_oltp.namespaces.annotations    IS 'Annotations do namespace no Kubernetes';
COMMENT ON COLUMN titlis_oltp.namespaces.updated_at     IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

-- ----------------------------------------------------------------
-- workloads  (espelha Kubernetes Deployments)
-- k8s_uid: UID do recurso K8s — usado para tag titlis_resource_uid
-- nos SLOs do Datadog (Path B de idempotência do SLOService).
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.workloads (
    workload_id           BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace_id          BIGINT      NOT NULL REFERENCES titlis_oltp.namespaces(namespace_id),
    workload_name         VARCHAR(255) NOT NULL,
    workload_kind         VARCHAR(100) NOT NULL DEFAULT 'Deployment',
    k8s_uid               VARCHAR(255),
    service_tier          titlis_oltp.service_tier,
    dd_git_repository_url VARCHAR(500),
    backstage_component   VARCHAR(255),
    owner_team            VARCHAR(255),
    labels                JSONB,
    annotations           JSONB,
    resource_version      VARCHAR(100),
    is_active             BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, workload_name, workload_kind)
);

COMMENT ON TABLE  titlis_oltp.workloads                        IS 'Deployments Kubernetes rastreados pelo operador (soft-delete via is_active)';
COMMENT ON COLUMN titlis_oltp.workloads.workload_id            IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.workloads.namespace_id           IS 'Namespace ao qual o workload pertence';
COMMENT ON COLUMN titlis_oltp.workloads.workload_name          IS 'Nome do Deployment no Kubernetes';
COMMENT ON COLUMN titlis_oltp.workloads.workload_kind          IS 'Tipo do recurso K8s; padrão Deployment, extensível para StatefulSet';
COMMENT ON COLUMN titlis_oltp.workloads.k8s_uid                IS 'metadata.uid do recurso K8s; usado como tag titlis_resource_uid no Datadog (Path B de idempotência do SLOService)';
COMMENT ON COLUMN titlis_oltp.workloads.service_tier           IS 'Criticidade do serviço: TIER_1 a TIER_4';
COMMENT ON COLUMN titlis_oltp.workloads.dd_git_repository_url  IS 'DD_GIT_REPOSITORY_URL do container; pré-condição de auto-remediação — ausência bloqueia criação de PR';
COMMENT ON COLUMN titlis_oltp.workloads.backstage_component    IS 'Nome do componente no catálogo Backstage';
COMMENT ON COLUMN titlis_oltp.workloads.owner_team             IS 'Time responsável obtido via labels ou Backstage';
COMMENT ON COLUMN titlis_oltp.workloads.resource_version       IS 'K8s resourceVersion para detecção de mudanças';
COMMENT ON COLUMN titlis_oltp.workloads.is_active              IS 'Soft-delete: workloads deletados do K8s mantêm histórico';
COMMENT ON COLUMN titlis_oltp.workloads.updated_at             IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

CREATE INDEX idx_workloads_k8s_uid ON titlis_oltp.workloads (k8s_uid) WHERE k8s_uid IS NOT NULL;

-- ----------------------------------------------------------------
-- validation_rules  (catálogo das 26+ regras — referência imutável)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.validation_rules (
    validation_rule_id   BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id              VARCHAR(50) NOT NULL UNIQUE,
    pillar               titlis_oltp.validation_pillar    NOT NULL,
    rule_severity        titlis_oltp.validation_severity  NOT NULL,
    rule_type            titlis_oltp.validation_rule_type NOT NULL,
    weight               NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    rule_name            VARCHAR(255) NOT NULL,
    description          TEXT,
    is_remediable        BOOLEAN     NOT NULL DEFAULT FALSE,
    remediation_category titlis_oltp.remediation_category,
    is_active            BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_oltp.validation_rules                        IS 'Catálogo imutável das 26+ regras de validação de workloads';
COMMENT ON COLUMN titlis_oltp.validation_rules.validation_rule_id     IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.validation_rules.rule_id                IS 'Código legível da regra: RES-001, PERF-002, etc.';
COMMENT ON COLUMN titlis_oltp.validation_rules.pillar                 IS 'Pilar de governança: RESILIENCE, SECURITY, COST, PERFORMANCE, OPERATIONAL, COMPLIANCE';
COMMENT ON COLUMN titlis_oltp.validation_rules.rule_severity          IS 'Severidade: CRITICAL > ERROR > WARNING > INFO > OPTIONAL';
COMMENT ON COLUMN titlis_oltp.validation_rules.rule_type              IS 'Tipo de validação: BOOLEAN, NUMERIC, ENUM, REGEX';
COMMENT ON COLUMN titlis_oltp.validation_rules.weight                 IS 'Peso da regra na composição do pillar score';
COMMENT ON COLUMN titlis_oltp.validation_rules.rule_name              IS 'Nome legível da regra para exibição em dashboards';
COMMENT ON COLUMN titlis_oltp.validation_rules.description            IS 'Descrição detalhada do critério de validação';
COMMENT ON COLUMN titlis_oltp.validation_rules.is_remediable          IS 'Indica se o operador pode gerar PR de remediação automaticamente';
COMMENT ON COLUMN titlis_oltp.validation_rules.remediation_category   IS 'Categoria da remediação: resources ou hpa; obrigatório quando is_remediable = true';
COMMENT ON COLUMN titlis_oltp.validation_rules.is_active              IS 'Permite desativar regras sem deletar histórico';
COMMENT ON COLUMN titlis_oltp.validation_rules.updated_at             IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

-- ----------------------------------------------------------------
-- app_scorecards  (estado atual — SCD Type 4 "current table")
-- UNIQUE(workload_id) garante 1 linha por workload.
-- A aplicação arquiva o estado anterior em app_scorecard_history
-- ao incrementar version (sem triggers DML).
-- tenant_id desnormalizado para performance de RLS sem join até clusters.
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.app_scorecards (
    app_scorecard_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id       BIGINT      NOT NULL REFERENCES titlis_oltp.workloads(workload_id),
    tenant_id         BIGINT      REFERENCES titlis_oltp.tenants(tenant_id),
    version           INTEGER     NOT NULL DEFAULT 1,
    overall_score     NUMERIC(5,2) NOT NULL CHECK (overall_score BETWEEN 0 AND 100),
    compliance_status titlis_oltp.compliance_status NOT NULL DEFAULT 'UNKNOWN',
    total_rules       INTEGER     NOT NULL DEFAULT 0,
    passed_rules      INTEGER     NOT NULL DEFAULT 0,
    failed_rules      INTEGER     NOT NULL DEFAULT 0,
    critical_failures INTEGER     NOT NULL DEFAULT 0,
    error_count       INTEGER     NOT NULL DEFAULT 0,
    warning_count     INTEGER     NOT NULL DEFAULT 0,
    evaluated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    k8s_event_type    VARCHAR(50),
    raw_metadata      JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workload_id)
);

COMMENT ON TABLE  titlis_oltp.app_scorecards                    IS 'Estado atual do scorecard de maturidade por workload (SCD Type 4 — 1 linha por workload)';
COMMENT ON COLUMN titlis_oltp.app_scorecards.app_scorecard_id   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.app_scorecards.workload_id        IS 'Workload avaliado; UNIQUE garante 1 scorecard por workload';
COMMENT ON COLUMN titlis_oltp.app_scorecards.tenant_id          IS 'Desnormalizado para performance de RLS sem join até clusters';
COMMENT ON COLUMN titlis_oltp.app_scorecards.version            IS 'Contador monotônico de avaliações; incremento sinaliza nova avaliação para a aplicação arquivar o estado anterior';
COMMENT ON COLUMN titlis_oltp.app_scorecards.overall_score      IS 'Score global de 0 a 100';
COMMENT ON COLUMN titlis_oltp.app_scorecards.compliance_status  IS 'Status de compliance: COMPLIANT, NON_COMPLIANT, UNKNOWN, PENDING';
COMMENT ON COLUMN titlis_oltp.app_scorecards.critical_failures  IS 'Regras CRITICAL que falharam; input primário para alertas e paginação';
COMMENT ON COLUMN titlis_oltp.app_scorecards.evaluated_at       IS 'Timestamp da avaliação pelo operador';
COMMENT ON COLUMN titlis_oltp.app_scorecards.k8s_event_type     IS 'Evento K8s que disparou a avaliação: resume, create ou update';
COMMENT ON COLUMN titlis_oltp.app_scorecards.raw_metadata       IS 'Labels, annotations e campos extras do body K8s para auditoria';
COMMENT ON COLUMN titlis_oltp.app_scorecards.updated_at         IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

-- ----------------------------------------------------------------
-- pillar_scores  (scores por pilar — estado atual)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.pillar_scores (
    pillar_score_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    app_scorecard_id BIGINT      NOT NULL REFERENCES titlis_oltp.app_scorecards(app_scorecard_id) ON DELETE CASCADE,
    pillar           titlis_oltp.validation_pillar NOT NULL,
    pillar_score     NUMERIC(5,2) NOT NULL CHECK (pillar_score BETWEEN 0 AND 100),
    passed_checks    INTEGER     NOT NULL DEFAULT 0,
    failed_checks    INTEGER     NOT NULL DEFAULT 0,
    weighted_score   NUMERIC(8,4),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (app_scorecard_id, pillar)
);

COMMENT ON TABLE  titlis_oltp.pillar_scores                    IS 'Score por pilar de governança do scorecard atual';
COMMENT ON COLUMN titlis_oltp.pillar_scores.pillar_score_id   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.pillar_scores.app_scorecard_id  IS 'Scorecard ao qual este score de pilar pertence';
COMMENT ON COLUMN titlis_oltp.pillar_scores.pillar            IS 'Pilar de governança avaliado';
COMMENT ON COLUMN titlis_oltp.pillar_scores.pillar_score      IS 'Score do pilar de 0 a 100';
COMMENT ON COLUMN titlis_oltp.pillar_scores.passed_checks     IS 'Número de regras aprovadas neste pilar';
COMMENT ON COLUMN titlis_oltp.pillar_scores.failed_checks     IS 'Número de regras reprovadas neste pilar';
COMMENT ON COLUMN titlis_oltp.pillar_scores.weighted_score    IS 'Score ponderado pelo weight das regras';
COMMENT ON COLUMN titlis_oltp.pillar_scores.updated_at        IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

-- ----------------------------------------------------------------
-- validation_results  (resultado por regra — estado atual)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.validation_results (
    validation_result_id BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    app_scorecard_id     BIGINT      NOT NULL REFERENCES titlis_oltp.app_scorecards(app_scorecard_id) ON DELETE CASCADE,
    validation_rule_id   BIGINT      NOT NULL REFERENCES titlis_oltp.validation_rules(validation_rule_id),
    rule_passed          BOOLEAN     NOT NULL,
    result_message       TEXT,
    actual_value         TEXT,
    evaluated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (app_scorecard_id, validation_rule_id)
);

COMMENT ON TABLE  titlis_oltp.validation_results                        IS 'Resultado de cada regra de validação no scorecard atual';
COMMENT ON COLUMN titlis_oltp.validation_results.validation_result_id   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.validation_results.app_scorecard_id       IS 'Scorecard ao qual este resultado pertence';
COMMENT ON COLUMN titlis_oltp.validation_results.validation_rule_id     IS 'Regra que foi avaliada';
COMMENT ON COLUMN titlis_oltp.validation_results.rule_passed            IS 'True se a regra foi aprovada, false se reprovada';
COMMENT ON COLUMN titlis_oltp.validation_results.result_message         IS 'Mensagem explicativa do resultado da validação';
COMMENT ON COLUMN titlis_oltp.validation_results.actual_value           IS 'Valor observado no Deployment durante a avaliação (ex: "100m" para CPU)';

-- ----------------------------------------------------------------
-- app_remediations  (estado atual — SCD Type 4 "current table")
-- A aplicação arquiva o estado anterior em remediation_history
-- a cada transição de app_remediation_status ou incremento de version
-- (sem triggers DML).
-- tenant_id desnormalizado para performance de RLS.
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.app_remediations (
    app_remediation_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id            BIGINT      NOT NULL REFERENCES titlis_oltp.workloads(workload_id),
    tenant_id              BIGINT      REFERENCES titlis_oltp.tenants(tenant_id),
    version                INTEGER     NOT NULL DEFAULT 1,
    app_scorecard_id       BIGINT      REFERENCES titlis_oltp.app_scorecards(app_scorecard_id),
    app_remediation_status titlis_oltp.remediation_status NOT NULL DEFAULT 'PENDING',
    github_pr_number       INTEGER,
    github_pr_url          VARCHAR(500),
    github_pr_title        VARCHAR(500),
    github_branch          VARCHAR(255),
    repository_url         VARCHAR(500),
    error_message          TEXT,
    triggered_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workload_id)
);

COMMENT ON TABLE  titlis_oltp.app_remediations                          IS 'Estado atual da remediação automática por workload (SCD Type 4 — 1 linha por workload)';
COMMENT ON COLUMN titlis_oltp.app_remediations.app_remediation_id       IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.app_remediations.workload_id              IS 'Workload remediado; UNIQUE garante 1 remediação ativa por workload';
COMMENT ON COLUMN titlis_oltp.app_remediations.tenant_id                IS 'Desnormalizado para performance de RLS sem join';
COMMENT ON COLUMN titlis_oltp.app_remediations.version                  IS 'Contador monotônico de tentativas de remediação';
COMMENT ON COLUMN titlis_oltp.app_remediations.app_scorecard_id         IS 'Scorecard que disparou esta remediação';
COMMENT ON COLUMN titlis_oltp.app_remediations.app_remediation_status   IS 'Estado atual: PENDING, IN_PROGRESS, PR_OPEN, PR_MERGED, FAILED, SKIPPED';
COMMENT ON COLUMN titlis_oltp.app_remediations.github_pr_number         IS 'Número do Pull Request criado no GitHub';
COMMENT ON COLUMN titlis_oltp.app_remediations.github_pr_url            IS 'URL completa do Pull Request no GitHub';
COMMENT ON COLUMN titlis_oltp.app_remediations.github_pr_title          IS 'Título do Pull Request criado';
COMMENT ON COLUMN titlis_oltp.app_remediations.github_branch            IS 'Branch criada para o PR (fix/auto-remediation-{namespace}-{resource}-*)';
COMMENT ON COLUMN titlis_oltp.app_remediations.repository_url           IS 'URL do repositório GitHub alvo da remediação';
COMMENT ON COLUMN titlis_oltp.app_remediations.error_message            IS 'Mensagem de erro em caso de falha na remediação';
COMMENT ON COLUMN titlis_oltp.app_remediations.triggered_at             IS 'Timestamp de início da tentativa de remediação';
COMMENT ON COLUMN titlis_oltp.app_remediations.resolved_at              IS 'Timestamp de conclusão (PR_MERGED ou FAILED)';
COMMENT ON COLUMN titlis_oltp.app_remediations.updated_at               IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

-- ----------------------------------------------------------------
-- remediation_issues  (issues individuais vinculadas à remediação)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.remediation_issues (
    remediation_issue_id BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    app_remediation_id   BIGINT      NOT NULL REFERENCES titlis_oltp.app_remediations(app_remediation_id) ON DELETE CASCADE,
    validation_rule_id   BIGINT      NOT NULL REFERENCES titlis_oltp.validation_rules(validation_rule_id),
    issue_category       titlis_oltp.remediation_category NOT NULL,
    description          TEXT,
    suggested_value      VARCHAR(100),
    applied_value        VARCHAR(100),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_oltp.remediation_issues                      IS 'Issues individuais de uma remediação, com os valores sugeridos e aplicados';
COMMENT ON COLUMN titlis_oltp.remediation_issues.remediation_issue_id IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.remediation_issues.app_remediation_id   IS 'Remediação à qual esta issue pertence';
COMMENT ON COLUMN titlis_oltp.remediation_issues.validation_rule_id   IS 'Regra de validação que motivou esta issue';
COMMENT ON COLUMN titlis_oltp.remediation_issues.issue_category       IS 'Categoria da issue: resources (CPU/mem) ou hpa';
COMMENT ON COLUMN titlis_oltp.remediation_issues.description          IS 'Descrição da issue identificada';
COMMENT ON COLUMN titlis_oltp.remediation_issues.suggested_value      IS 'Valor calculado pelo operador antes de aplicar _keep_max';
COMMENT ON COLUMN titlis_oltp.remediation_issues.applied_value        IS 'Valor efetivamente aplicado no PR; pode diferir de suggested_value por _keep_max';

-- ----------------------------------------------------------------
-- slo_configs  (estado atual dos SLOs — espelha SLOConfig CRD)
--
-- Colunas de auto-detecção de framework (implementadas no operador):
--   auto_detect_framework: flag do spec — ativa detecção automática
--   app_framework: framework explícito do spec (quando não usa auto-detect)
--   detected_framework: resultado da detecção — persiste em status.detected_framework
--   detection_source: annotation | datadog_tag | fallback (H-13)
--   k8s_resource_uid: metadata.uid do SLOConfig CRD — usado para tag
--     titlis_resource_uid:<uid> no Datadog (Path B idempotência do SLOService)
-- ----------------------------------------------------------------
CREATE TABLE titlis_oltp.slo_configs (
    slo_config_id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace_id          BIGINT      NOT NULL REFERENCES titlis_oltp.namespaces(namespace_id),
    tenant_id             BIGINT      REFERENCES titlis_oltp.tenants(tenant_id),
    slo_config_name       VARCHAR(255) NOT NULL,
    slo_type              titlis_oltp.slo_type      NOT NULL,
    timeframe             titlis_oltp.slo_timeframe NOT NULL,
    target                NUMERIC(6,4) NOT NULL CHECK (target BETWEEN 0 AND 100),
    warning               NUMERIC(6,4)              CHECK (warning BETWEEN 0 AND 100),
    auto_detect_framework BOOLEAN     NOT NULL DEFAULT FALSE,
    app_framework         titlis_oltp.slo_app_framework,
    detected_framework    VARCHAR(50),
    detection_source      VARCHAR(50),
    k8s_resource_uid      VARCHAR(255),
    datadog_slo_id        VARCHAR(255),
    datadog_slo_state     titlis_oltp.slo_state,
    last_sync_at          TIMESTAMPTZ,
    sync_error            TEXT,
    spec_raw              JSONB,
    version               INTEGER     NOT NULL DEFAULT 1,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, slo_config_name),
    CONSTRAINT chk_warning_lt_target CHECK (warning IS NULL OR warning < target)
);

COMMENT ON TABLE  titlis_oltp.slo_configs                          IS 'Estado atual dos SLOs — espelha SLOConfig CRDs do Kubernetes';
COMMENT ON COLUMN titlis_oltp.slo_configs.slo_config_id            IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_oltp.slo_configs.namespace_id             IS 'Namespace Kubernetes onde o SLOConfig CRD está criado';
COMMENT ON COLUMN titlis_oltp.slo_configs.tenant_id                IS 'Desnormalizado para performance de RLS sem join';
COMMENT ON COLUMN titlis_oltp.slo_configs.slo_config_name          IS 'Nome do SLOConfig CRD no Kubernetes';
COMMENT ON COLUMN titlis_oltp.slo_configs.slo_type                 IS 'Tipo de SLO: METRIC, MONITOR ou TIME_SLICE';
COMMENT ON COLUMN titlis_oltp.slo_configs.timeframe                IS 'Janela de conformidade: 7d, 30d ou 90d';
COMMENT ON COLUMN titlis_oltp.slo_configs.target                   IS 'Meta de conformidade (0–100)';
COMMENT ON COLUMN titlis_oltp.slo_configs.warning                  IS 'Limiar de aviso; deve ser menor que target';
COMMENT ON COLUMN titlis_oltp.slo_configs.auto_detect_framework    IS 'Flag do spec que ativa detecção automática de framework via annotation K8s ou tag Datadog';
COMMENT ON COLUMN titlis_oltp.slo_configs.app_framework            IS 'Framework explícito do spec quando auto_detect_framework = false';
COMMENT ON COLUMN titlis_oltp.slo_configs.detected_framework       IS 'Framework detectado automaticamente; persiste status.detected_framework do CRD';
COMMENT ON COLUMN titlis_oltp.slo_configs.detection_source         IS 'Origem da detecção: annotation, datadog_tag ou fallback (ver H-13)';
COMMENT ON COLUMN titlis_oltp.slo_configs.k8s_resource_uid         IS 'metadata.uid do SLOConfig CRD; usado como tag titlis_resource_uid no Datadog (Path B de idempotência)';
COMMENT ON COLUMN titlis_oltp.slo_configs.datadog_slo_id           IS 'ID do SLO gerado no Datadog após criação';
COMMENT ON COLUMN titlis_oltp.slo_configs.datadog_slo_state        IS 'Estado de conformidade atual no Datadog: ok, warning, error, no_data';
COMMENT ON COLUMN titlis_oltp.slo_configs.last_sync_at             IS 'Timestamp da última sincronização bem-sucedida com Datadog';
COMMENT ON COLUMN titlis_oltp.slo_configs.sync_error               IS 'Mensagem de erro da última sincronização com Datadog';
COMMENT ON COLUMN titlis_oltp.slo_configs.spec_raw                 IS 'Spec completo do CRD em JSONB para auditoria e debug';
COMMENT ON COLUMN titlis_oltp.slo_configs.version                  IS 'Contador de atualizações do SLOConfig';
COMMENT ON COLUMN titlis_oltp.slo_configs.updated_at               IS 'Data da última modificação; atualizada pela aplicação a cada UPDATE';

CREATE INDEX idx_slo_datadog_id         ON titlis_oltp.slo_configs (datadog_slo_id);
CREATE INDEX idx_slo_k8s_uid            ON titlis_oltp.slo_configs (k8s_resource_uid) WHERE k8s_resource_uid IS NOT NULL;
CREATE INDEX idx_slo_detection_source   ON titlis_oltp.slo_configs (detection_source);

-- ================================================================
-- SCHEMA: titlis_audit — Histórico e Auditoria (SCD Type 4)
-- ================================================================

-- ----------------------------------------------------------------
-- app_scorecard_history
-- Sem FK para workload_id — registros históricos devem sobreviver
-- mesmo se o workload for deletado (soft-delete em workloads).
-- Snapshot JSONB de pillar_scores e validation_results elimina
-- joins custosos em queries analíticas.
-- A aplicação insere nesta tabela ao incrementar version em app_scorecards.
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.app_scorecard_history (
    app_scorecard_history_id BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id              BIGINT      NOT NULL,
    tenant_id                BIGINT,
    scorecard_version        INTEGER     NOT NULL,
    overall_score            NUMERIC(5,2) NOT NULL,
    compliance_status        VARCHAR(50) NOT NULL,
    total_rules              INTEGER     NOT NULL,
    passed_rules             INTEGER     NOT NULL,
    failed_rules             INTEGER     NOT NULL,
    critical_failures        INTEGER     NOT NULL,
    error_count              INTEGER     NOT NULL,
    warning_count            INTEGER     NOT NULL,
    pillar_scores            JSONB       NOT NULL,
    validation_results       JSONB       NOT NULL,
    evaluated_at             TIMESTAMPTZ NOT NULL,
    k8s_event_type           VARCHAR(50),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_audit.app_scorecard_history                         IS 'Histórico de scorecards arquivados pela aplicação antes de cada sobrescrita (SCD Type 4)';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.app_scorecard_history_id IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.workload_id              IS 'Referência lógica ao workload (sem FK constraint — sobrevive à deleção do workload)';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.tenant_id                IS 'Referência lógica ao tenant (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.scorecard_version        IS 'Versão arquivada do scorecard (version anterior ao incremento)';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.pillar_scores            IS 'Snapshot JSONB dos pillar_scores no momento do arquivamento: [{pillar, score, passed_checks, failed_checks, weighted_score}]';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.validation_results       IS 'Snapshot JSONB dos validation_results no momento do arquivamento: [{rule_id, rule_ref, pillar, severity, passed, message, actual_value}]';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.evaluated_at             IS 'Timestamp da avaliação original (não do arquivamento)';
COMMENT ON COLUMN titlis_audit.app_scorecard_history.k8s_event_type           IS 'Evento K8s que disparou a avaliação arquivada: resume, create ou update';

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
    pillar_score_history_id BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id             BIGINT      NOT NULL,
    tenant_id               BIGINT,
    scorecard_version       INTEGER     NOT NULL,
    pillar                  VARCHAR(50) NOT NULL,
    pillar_score            NUMERIC(5,2) NOT NULL,
    passed_checks           INTEGER     NOT NULL,
    failed_checks           INTEGER     NOT NULL,
    weighted_score          NUMERIC(8,4),
    evaluated_at            TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_audit.pillar_score_history                          IS 'Histórico de scores por pilar para gráficos de evolução; inserido pela aplicação junto com app_scorecard_history';
COMMENT ON COLUMN titlis_audit.pillar_score_history.pillar_score_history_id  IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_audit.pillar_score_history.workload_id              IS 'Referência lógica ao workload (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.pillar_score_history.tenant_id                IS 'Referência lógica ao tenant (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.pillar_score_history.pillar_score             IS 'Score do pilar de 0 a 100 no momento do arquivamento';

CREATE INDEX idx_pillar_hist_workload_pillar_time
    ON titlis_audit.pillar_score_history (workload_id, pillar, evaluated_at DESC);

-- ----------------------------------------------------------------
-- remediation_history  (log de todas as transições de estado)
-- A aplicação insere nesta tabela a cada transição de
-- app_remediation_status ou incremento de version em app_remediations
-- (sem triggers DML).
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.remediation_history (
    remediation_history_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id                     BIGINT      NOT NULL,
    tenant_id                       BIGINT,
    remediation_version             INTEGER     NOT NULL,
    app_remediation_status          VARCHAR(50) NOT NULL,
    previous_app_remediation_status VARCHAR(50),
    scorecard_version               INTEGER,
    github_pr_number                INTEGER,
    github_pr_url                   VARCHAR(500),
    github_branch                   VARCHAR(255),
    repository_url                  VARCHAR(500),
    issues_snapshot                 JSONB,
    error_message                   TEXT,
    triggered_at                    TIMESTAMPTZ NOT NULL,
    resolved_at                     TIMESTAMPTZ,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_audit.remediation_history                                          IS 'Log imutável de todas as transições de estado de remediação; inserido pela aplicação a cada mudança de app_remediation_status';
COMMENT ON COLUMN titlis_audit.remediation_history.remediation_history_id                   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_audit.remediation_history.workload_id                              IS 'Referência lógica ao workload (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.remediation_history.tenant_id                                IS 'Referência lógica ao tenant (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.remediation_history.remediation_version                      IS 'Versão da remediação no momento do registro';
COMMENT ON COLUMN titlis_audit.remediation_history.app_remediation_status                   IS 'Estado novo da remediação no momento do registro';
COMMENT ON COLUMN titlis_audit.remediation_history.previous_app_remediation_status          IS 'Estado anterior; permite reconstruir a máquina de estados';
COMMENT ON COLUMN titlis_audit.remediation_history.issues_snapshot                          IS 'Snapshot das issues de remediação no momento da transição';

CREATE INDEX idx_remediation_hist_workload_time
    ON titlis_audit.remediation_history (workload_id, triggered_at DESC);

CREATE INDEX idx_remediation_hist_status
    ON titlis_audit.remediation_history (app_remediation_status, created_at DESC);

-- ----------------------------------------------------------------
-- slo_compliance_history  (histórico de conformidade e sincronização)
-- detected_framework / detection_source: auditoria de H-13
-- (framework detectado incorretamente → fallback WSGI inesperado).
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.slo_compliance_history (
    slo_compliance_history_id BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slo_config_id             BIGINT      NOT NULL,
    namespace_id              BIGINT      NOT NULL,
    tenant_id                 BIGINT,
    slo_config_name           VARCHAR(255) NOT NULL,
    datadog_slo_id            VARCHAR(255),
    slo_type                  VARCHAR(50) NOT NULL,
    timeframe                 VARCHAR(10) NOT NULL,
    target                    NUMERIC(6,4) NOT NULL,
    actual_value              NUMERIC(6,4),
    slo_state                 VARCHAR(50),
    sync_action               VARCHAR(50),
    sync_error                TEXT,
    detected_framework        VARCHAR(50),
    detection_source          VARCHAR(50),
    recorded_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_audit.slo_compliance_history                        IS 'Histórico de sincronizações de SLO com o Datadog; cada chamada ao SLOService gera um registro';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.slo_compliance_history_id IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.slo_config_id          IS 'Referência lógica ao slo_config (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.namespace_id           IS 'Referência lógica ao namespace (sem FK constraint)';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.slo_config_name        IS 'Nome do SLOConfig no momento da sincronização';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.slo_type               IS 'Tipo de SLO no momento da sincronização: METRIC, MONITOR, TIME_SLICE';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.actual_value           IS 'Percentual de conformidade real obtido do Datadog';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.slo_state              IS 'Estado de conformidade no Datadog: ok, warning, error, no_data';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.sync_action            IS 'Ação executada: created, updated, noop ou error';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.detected_framework     IS 'Framework detectado nesta sincronização (auditoria de H-13)';
COMMENT ON COLUMN titlis_audit.slo_compliance_history.detection_source       IS 'Origem da detecção: annotation, datadog_tag ou fallback';

CREATE INDEX idx_slo_hist_config_time
    ON titlis_audit.slo_compliance_history (slo_config_id, recorded_at DESC);

CREATE INDEX idx_slo_hist_detection
    ON titlis_audit.slo_compliance_history (detected_framework, detection_source)
    WHERE detected_framework IS NOT NULL;

-- ----------------------------------------------------------------
-- notification_log  (auditoria de todas as notificações Slack)
-- ----------------------------------------------------------------
CREATE TABLE titlis_audit.notification_log (
    notification_log_id   BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id           BIGINT,
    namespace_id          BIGINT,
    tenant_id             BIGINT,
    notification_type     VARCHAR(50) NOT NULL,
    notification_severity titlis_oltp.notification_severity NOT NULL,
    channel               VARCHAR(255),
    notification_title    VARCHAR(500),
    message_preview       VARCHAR(500),
    sent_at               TIMESTAMPTZ,
    success               BOOLEAN     NOT NULL DEFAULT FALSE,
    error_message         TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_audit.notification_log                         IS 'Auditoria de todas as notificações Slack enviadas pelo operador';
COMMENT ON COLUMN titlis_audit.notification_log.notification_log_id    IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_audit.notification_log.workload_id            IS 'Referência lógica ao workload; NULL quando for digest de namespace';
COMMENT ON COLUMN titlis_audit.notification_log.namespace_id           IS 'Referência lógica ao namespace';
COMMENT ON COLUMN titlis_audit.notification_log.notification_type      IS 'Tipo de notificação: scorecard, remediation, digest ou slo';
COMMENT ON COLUMN titlis_audit.notification_log.notification_severity  IS 'Severidade: INFO, WARNING, ERROR, CRITICAL';
COMMENT ON COLUMN titlis_audit.notification_log.channel                IS 'Canal Slack destino da notificação';
COMMENT ON COLUMN titlis_audit.notification_log.notification_title     IS 'Título da notificação Slack';
COMMENT ON COLUMN titlis_audit.notification_log.message_preview        IS 'Primeiros 500 caracteres do corpo da mensagem para auditoria rápida';
COMMENT ON COLUMN titlis_audit.notification_log.sent_at                IS 'Timestamp de envio ao Slack';
COMMENT ON COLUMN titlis_audit.notification_log.success                IS 'True se o envio foi confirmado pela API do Slack';
COMMENT ON COLUMN titlis_audit.notification_log.error_message          IS 'Mensagem de erro em caso de falha no envio';

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
    resource_metric_id    BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id           BIGINT      NOT NULL,
    tenant_id             BIGINT,
    container_name        VARCHAR(255),
    metric_source         VARCHAR(50) NOT NULL DEFAULT 'datadog',
    cpu_avg_millicores    NUMERIC(10,3),
    cpu_p95_millicores    NUMERIC(10,3),
    mem_avg_mib           NUMERIC(10,3),
    mem_p95_mib           NUMERIC(10,3),
    suggested_cpu_request VARCHAR(50),
    suggested_cpu_limit   VARCHAR(50),
    suggested_mem_request VARCHAR(50),
    suggested_mem_limit   VARCHAR(50),
    sample_window         VARCHAR(20),
    collected_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_ts.resource_metrics                          IS 'Métricas de CPU e memória coletadas do Datadog por workload (série temporal; candidato a hypertable TimescaleDB)';
COMMENT ON COLUMN titlis_ts.resource_metrics.resource_metric_id      IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_ts.resource_metrics.workload_id             IS 'Referência lógica ao workload';
COMMENT ON COLUMN titlis_ts.resource_metrics.tenant_id               IS 'Referência lógica ao tenant';
COMMENT ON COLUMN titlis_ts.resource_metrics.container_name          IS 'Nome do container dentro do pod';
COMMENT ON COLUMN titlis_ts.resource_metrics.metric_source           IS 'Origem das métricas (datadog)';
COMMENT ON COLUMN titlis_ts.resource_metrics.cpu_avg_millicores      IS 'Média de consumo de CPU em millicores; input para suggest_cpu_request()';
COMMENT ON COLUMN titlis_ts.resource_metrics.cpu_p95_millicores      IS 'Percentil 95 de CPU em millicores; input para suggest_cpu_limit()';
COMMENT ON COLUMN titlis_ts.resource_metrics.mem_avg_mib             IS 'Média de consumo de memória em MiB; input para suggest_memory_request()';
COMMENT ON COLUMN titlis_ts.resource_metrics.mem_p95_mib             IS 'Percentil 95 de memória em MiB; input para suggest_memory_limit()';
COMMENT ON COLUMN titlis_ts.resource_metrics.suggested_cpu_request   IS 'CPU request sugerido pelo operador conforme lógica _keep_max / suggest_*()';
COMMENT ON COLUMN titlis_ts.resource_metrics.suggested_cpu_limit     IS 'CPU limit sugerido pelo operador';
COMMENT ON COLUMN titlis_ts.resource_metrics.suggested_mem_request   IS 'Memory request sugerido pelo operador';
COMMENT ON COLUMN titlis_ts.resource_metrics.suggested_mem_limit     IS 'Memory limit sugerido pelo operador';
COMMENT ON COLUMN titlis_ts.resource_metrics.sample_window           IS 'Janela de amostragem: 1h, 24h ou 7d';
COMMENT ON COLUMN titlis_ts.resource_metrics.collected_at            IS 'Timestamp da coleta';

CREATE INDEX idx_resource_metrics_workload_time
    ON titlis_ts.resource_metrics (workload_id, collected_at DESC);

-- ----------------------------------------------------------------
-- scorecard_scores  (série temporal plana para dashboards Grafana/Metabase)
-- Desnormalizado intencionalmente: elimina joins em tempo de query.
-- ----------------------------------------------------------------
CREATE TABLE titlis_ts.scorecard_scores (
    scorecard_score_id BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_id        BIGINT      NOT NULL,
    tenant_id          BIGINT,
    overall_score      NUMERIC(5,2) NOT NULL,
    resilience_score   NUMERIC(5,2),
    security_score     NUMERIC(5,2),
    cost_score         NUMERIC(5,2),
    performance_score  NUMERIC(5,2),
    operational_score  NUMERIC(5,2),
    compliance_score   NUMERIC(5,2),
    compliance_status  VARCHAR(50) NOT NULL,
    passed_rules       INTEGER,
    failed_rules       INTEGER,
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  titlis_ts.scorecard_scores                      IS 'Série temporal plana de scores para dashboards Grafana/Metabase (desnormalizado — sem joins em tempo de query)';
COMMENT ON COLUMN titlis_ts.scorecard_scores.scorecard_score_id   IS 'Chave primária surrogate';
COMMENT ON COLUMN titlis_ts.scorecard_scores.workload_id          IS 'Referência lógica ao workload';
COMMENT ON COLUMN titlis_ts.scorecard_scores.tenant_id            IS 'Referência lógica ao tenant';
COMMENT ON COLUMN titlis_ts.scorecard_scores.overall_score        IS 'Score global de 0 a 100';
COMMENT ON COLUMN titlis_ts.scorecard_scores.compliance_status    IS 'Status de compliance no momento do registro: COMPLIANT, NON_COMPLIANT, UNKNOWN, PENDING';
COMMENT ON COLUMN titlis_ts.scorecard_scores.recorded_at          IS 'Timestamp de inserção; eixo X de todos os gráficos de evolução';

CREATE INDEX idx_score_ts_workload_time
    ON titlis_ts.scorecard_scores (workload_id, recorded_at DESC);

-- índice por tempo sem predicado now() — predicado com now() vira constante
-- em índices parciais e fica obsoleto. Usar filtro na query.
CREATE INDEX idx_score_ts_recorded_at
    ON titlis_ts.scorecard_scores (recorded_at DESC);

-- ================================================================
-- VIEWS — abstração para Frontend e APIs
-- ================================================================

-- Dashboard principal: estado atual de todos os workloads
CREATE OR REPLACE VIEW titlis_oltp.v_workload_dashboard AS
SELECT
    w.workload_id,
    c.cluster_name,
    c.environment,
    c.tenant_id,
    n.namespace_name            AS namespace,
    w.workload_name,
    w.workload_kind,
    w.service_tier,
    w.owner_team,
    sc.overall_score,
    sc.compliance_status,
    sc.passed_rules,
    sc.failed_rules,
    sc.critical_failures,
    sc.version                  AS scorecard_version,
    sc.evaluated_at,
    ar.app_remediation_status   AS remediation_status,
    ar.github_pr_url,
    ar.github_pr_number,
    sc.updated_at               AS last_scored_at
FROM titlis_oltp.workloads w
JOIN titlis_oltp.namespaces n      ON n.namespace_id = w.namespace_id
JOIN titlis_oltp.clusters c        ON c.cluster_id = n.cluster_id
LEFT JOIN titlis_oltp.app_scorecards sc ON sc.workload_id = w.workload_id
LEFT JOIN titlis_oltp.app_remediations ar ON ar.workload_id = w.workload_id
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
-- Requer que a aplicação insira validation_results JSONB com estrutura:
-- [{rule_id, rule_ref, pillar, severity, passed, message, actual_value}]
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
    COUNT(*)                                                                   AS total_attempts,
    COUNT(*) FILTER (WHERE app_remediation_status = 'PR_MERGED')               AS successful,
    COUNT(*) FILTER (WHERE app_remediation_status = 'FAILED')                  AS failed,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE app_remediation_status = 'PR_MERGED')
        / NULLIF(COUNT(*), 0), 2
    )                                                                          AS success_rate_pct,
    MAX(triggered_at)                                                          AS last_attempt_at
FROM titlis_audit.remediation_history
GROUP BY workload_id;

-- Diagnóstico de detecção de framework (checklist semanal H-13)
-- Lista SLOs com detection_source=fallback — possível framework errado.
CREATE OR REPLACE VIEW titlis_oltp.v_slo_framework_detection AS
SELECT
    n.namespace_name            AS namespace,
    sc.slo_config_name          AS slo_name,
    sc.slo_type,
    sc.auto_detect_framework,
    sc.app_framework            AS explicit_framework,
    sc.detected_framework,
    sc.detection_source,
    sc.datadog_slo_id,
    sc.datadog_slo_state,
    sc.last_sync_at,
    sc.sync_error
FROM titlis_oltp.slo_configs sc
JOIN titlis_oltp.namespaces n ON n.namespace_id = sc.namespace_id
ORDER BY
    (sc.detection_source = 'fallback') DESC,
    sc.last_sync_at DESC NULLS LAST;

-- ================================================================
-- INDEXES DE PERFORMANCE
-- ================================================================

-- OLTP — leituras do frontend
CREATE INDEX idx_workloads_namespace      ON titlis_oltp.workloads (namespace_id)
    WHERE is_active = TRUE;
CREATE INDEX idx_scorecard_compliance     ON titlis_oltp.app_scorecards (compliance_status);
CREATE INDEX idx_scorecard_score          ON titlis_oltp.app_scorecards (overall_score);
CREATE INDEX idx_remediation_status       ON titlis_oltp.app_remediations (app_remediation_status);
CREATE INDEX idx_val_results_rule_passed  ON titlis_oltp.validation_results (validation_rule_id, rule_passed);

-- ================================================================
-- ROLES E PERMISSÕES
-- ================================================================
-- Criar roles antes de executar este bloco em produção:
--   CREATE ROLE operator_role;
--   CREATE ROLE analytics_role;
--   CREATE ROLE metrics_collector_role;
--
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA titlis_oltp TO operator_role;
-- GRANT SELECT ON ALL TABLES IN SCHEMA titlis_audit TO analytics_role;
-- GRANT INSERT ON ALL TABLES IN SCHEMA titlis_ts TO metrics_collector_role;
--
-- RLS (Fase 1 — multi-tenant):
--   ALTER TABLE titlis_oltp.clusters ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY tenant_isolation ON titlis_oltp.clusters
--       USING (tenant_id = current_setting('app.current_tenant_id')::BIGINT);
--   (repetir para todas as tabelas com tenant_id)

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
