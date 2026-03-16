# Titlis API — Guia de Implementação Kotlin

> **Stack:** Kotlin 2.x · Ktor 3.x · Exposed ORM · HikariCP · PostgreSQL 15+
> **Protocolo de telemetria:** TitlisUDP (JSON-over-UDP — porta 8125)
> **API REST:** Ktor HTTP (porta 8080)
> **Status:** documentação de implementação — banco criado, não integrado

---

## Por Que Essa Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                      Titlis Operator (Python)                    │
│                                                                  │
│  ScorecardController ──► UDP sender ──────────────────────────┐  │
│  SLOController       ──► UDP sender ──────────────────────────┤  │
│  RemediationService  ──► UDP sender ──────────────────────────┤  │
│  SlackService        ──► UDP sender (notification log) ───────┤  │
│                                                                │  │
│  SLOService          ──► HTTP GET /slo-configs/{name} ────────┤  │
│  RemediationService  ──► HTTP GET /remediations/{workload} ───┘  │
└─────────────────────────────────────────────────────────────────┘
         │ UDP :8125  (fire-and-forget, sem ACK)
         │ HTTP :8080 (reads + upserts críticos)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Titlis API (Kotlin/Ktor)                   │
│                                                                  │
│  UDP Server ──► EventRouter ──► Repository ──► PostgreSQL        │
│  REST Server ──► Routes ──────► Repository ──► PostgreSQL        │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  PostgreSQL 15+                                                  │
│  titlis_oltp | titlis_audit | titlis_ts                         │
└─────────────────────────────────────────────────────────────────┘
```

**UDP para escrita assíncrona:** o operador avalia scorecards a cada `RECONCILE_INTERVAL_SECONDS=300`
e não deve bloquear aguardando confirmação de banco. UDP fire-and-forget mantém a latência do operador
estável independente da saúde do banco.

**HTTP para leituras e escritas críticas:** operações que precisam de resposta (buscar estado atual
de um SLO, verificar se existe remediação aberta, upserts que precisam de confirmação).

**Por que não StatsD puro:** StatsD só transporta métricas numéricas no formato `nome:valor|tipo`.
Os eventos do Titlis são objetos de domínio ricos (scorecards com 26 regras, pillar scores, etc.).
O protocolo TitlisUDP usa JSON sobre UDP — mantém a semântica fire-and-forget do StatsD com payloads
estruturados adequados ao domínio.

---

## Pré-requisitos

```bash
# JDK 21+
java -version  # >= 21

# Gradle wrapper (o projeto gera o próprio)
# IntelliJ IDEA ou qualquer editor com suporte Kotlin

# PostgreSQL rodando com o schema criado
psql -U postgres -d titlis -f db/schema.sql
```

---

## Passo 1 — Estrutura do Projeto Kotlin

### 1.1 Criar o projeto

```bash
# Dentro do repositório jt-operator:
mkdir -p titlis-api/src/main/kotlin/io/titlis/api
mkdir -p titlis-api/src/main/resources
mkdir -p titlis-api/src/test/kotlin/io/titlis/api

cd titlis-api
```

### 1.2 `build.gradle.kts`

```kotlin
plugins {
    kotlin("jvm") version "2.1.0"
    kotlin("plugin.serialization") version "2.1.0"
    id("io.ktor.plugin") version "3.1.0"
    application
}

group = "io.titlis"
version = "0.1.0"

application {
    mainClass.set("io.titlis.api.MainKt")
}

repositories {
    mavenCentral()
}

val ktorVersion = "3.1.0"
val exposedVersion = "0.60.0"
val hikariVersion = "6.2.1"
val postgresVersion = "42.7.5"
val logbackVersion = "1.5.18"

dependencies {
    // Ktor server
    implementation("io.ktor:ktor-server-core:$ktorVersion")
    implementation("io.ktor:ktor-server-netty:$ktorVersion")
    implementation("io.ktor:ktor-server-content-negotiation:$ktorVersion")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktorVersion")
    implementation("io.ktor:ktor-server-status-pages:$ktorVersion")
    implementation("io.ktor:ktor-server-call-logging:$ktorVersion")

    // Database
    implementation("org.jetbrains.exposed:exposed-core:$exposedVersion")
    implementation("org.jetbrains.exposed:exposed-dao:$exposedVersion")
    implementation("org.jetbrains.exposed:exposed-jdbc:$exposedVersion")
    implementation("org.jetbrains.exposed:exposed-kotlin-datetime:$exposedVersion")
    implementation("org.jetbrains.exposed:exposed-json:$exposedVersion")
    implementation("com.zaxxer:HikariCP:$hikariVersion")
    implementation("org.postgresql:postgresql:$postgresVersion")

    // Serialization
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.0")
    implementation("org.jetbrains.kotlinx:kotlinx-datetime:0.6.2")

    // Logging
    implementation("ch.qos.logback:logback-classic:$logbackVersion")
    implementation("net.logstash.logback:logstash-logback-encoder:8.0")

    // Tests
    testImplementation(kotlin("test"))
    testImplementation("io.ktor:ktor-server-test-host:$ktorVersion")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.1")
    testImplementation("io.mockk:mockk:1.13.16")
    testImplementation("com.h2database:h2:2.3.232")  // in-memory DB para testes
}

tasks.test {
    useJUnitPlatform()
}

kotlin {
    jvmToolchain(21)
}
```

### 1.3 Estrutura de diretórios

```
titlis-api/
├── build.gradle.kts
├── gradle/
│   └── wrapper/
├── src/
│   ├── main/
│   │   ├── kotlin/io/titlis/api/
│   │   │   ├── Main.kt                        # entry point
│   │   │   ├── config/
│   │   │   │   └── AppConfig.kt               # leitura de ENV vars
│   │   │   ├── database/
│   │   │   │   ├── DatabaseFactory.kt         # HikariCP + Exposed setup
│   │   │   │   └── tables/                    # Exposed Table objects
│   │   │   │       ├── OltpTables.kt
│   │   │   │       ├── AuditTables.kt
│   │   │   │       └── TsTables.kt
│   │   │   ├── domain/
│   │   │   │   └── Events.kt                  # data classes de eventos UDP
│   │   │   ├── repository/
│   │   │   │   ├── ScorecardRepository.kt
│   │   │   │   ├── RemediationRepository.kt
│   │   │   │   ├── SloRepository.kt
│   │   │   │   └── MetricsRepository.kt
│   │   │   ├── udp/
│   │   │   │   ├── UdpServer.kt               # servidor UDP coroutines
│   │   │   │   └── EventRouter.kt             # roteamento de eventos
│   │   │   └── routes/
│   │   │       ├── ScorecardRoutes.kt
│   │   │       ├── RemediationRoutes.kt
│   │   │       ├── SloRoutes.kt
│   │   │       └── HealthRoutes.kt
│   │   └── resources/
│   │       ├── application.conf               # Ktor config
│   │       └── logback.xml
│   └── test/
│       └── kotlin/io/titlis/api/
│           ├── udp/UdpServerTest.kt
│           └── routes/ScorecardRoutesTest.kt
```

---

## Passo 2 — Configuração

### 2.1 `src/main/resources/application.conf`

```hocon
ktor {
    deployment {
        port = 8080
        port = ${?PORT}
    }
    application {
        modules = [io.titlis.api.MainKt.module]
    }
}

titlis {
    database {
        url      = "jdbc:postgresql://localhost:5432/titlis"
        url      = ${?DATABASE_URL}
        user     = "postgres"
        user     = ${?DATABASE_USER}
        password = "postgres"
        password = ${?DATABASE_PASSWORD}
        pool {
            maxPoolSize       = 10
            maxPoolSize       = ${?DB_POOL_MAX}
            connectionTimeout = 30000
            idleTimeout       = 600000
        }
    }
    udp {
        port        = 8125
        port        = ${?TITLIS_UDP_PORT}
        bufferSize  = 65507   # max UDP payload IPv4
        workers     = 4       # coroutines processando a fila
        queueSize   = 10000   # buffer interno antes de processar
    }
}
```

### 2.2 `src/main/kotlin/io/titlis/api/config/AppConfig.kt`

```kotlin
import com.typesafe.config.Config

data class DatabaseConfig(
    val url: String,
    val user: String,
    val password: String,
    val maxPoolSize: Int,
    val connectionTimeout: Long,
    val idleTimeout: Long,
)

data class UdpConfig(
    val port: Int,
    val bufferSize: Int,
    val workers: Int,
    val queueSize: Int,
)

data class AppConfig(
    val database: DatabaseConfig,
    val udp: UdpConfig,
) {
    companion object {
        fun from(config: Config): AppConfig {
            val db = config.getConfig("titlis.database")
            val udp = config.getConfig("titlis.udp")
            return AppConfig(
                database = DatabaseConfig(
                    url = db.getString("url"),
                    user = db.getString("user"),
                    password = db.getString("password"),
                    maxPoolSize = db.getInt("pool.maxPoolSize"),
                    connectionTimeout = db.getLong("pool.connectionTimeout"),
                    idleTimeout = db.getLong("pool.idleTimeout"),
                ),
                udp = UdpConfig(
                    port = udp.getInt("port"),
                    bufferSize = udp.getInt("bufferSize"),
                    workers = udp.getInt("workers"),
                    queueSize = udp.getInt("queueSize"),
                ),
            )
        }
    }
}
```

---

## Passo 3 — Camada de Banco (Exposed ORM)

### 3.1 `database/DatabaseFactory.kt`

```kotlin
package io.titlis.api.database

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import io.titlis.api.config.DatabaseConfig
import kotlinx.coroutines.Dispatchers
import org.jetbrains.exposed.sql.Database
import org.jetbrains.exposed.sql.transactions.experimental.newSuspendedTransaction

object DatabaseFactory {
    fun init(config: DatabaseConfig) {
        val hikariConfig = HikariConfig().apply {
            jdbcUrl = config.url
            username = config.user
            password = config.password
            maximumPoolSize = config.maxPoolSize
            connectionTimeout = config.connectionTimeout
            idleTimeout = config.idleTimeout
            driverClassName = "org.postgresql.Driver"
            isAutoCommit = false
            transactionIsolation = "TRANSACTION_REPEATABLE_READ"
            validate()
        }
        Database.connect(HikariDataSource(hikariConfig))
    }

    suspend fun <T> dbQuery(block: suspend () -> T): T =
        newSuspendedTransaction(Dispatchers.IO) { block() }
}
```

### 3.2 `database/tables/OltpTables.kt`

```kotlin
package io.titlis.api.database.tables

import org.jetbrains.exposed.sql.Table
import org.jetbrains.exposed.sql.kotlin.datetime.timestampWithTimeZone

object Clusters : Table("titlis_oltp.clusters") {
    val id          = uuid("id").autoGenerate()
    val name        = varchar("name", 255)
    val environment = varchar("environment", 100)
    val region      = varchar("region", 100).nullable()
    val provider    = varchar("provider", 100).nullable()
    val k8sVersion  = varchar("k8s_version", 50).nullable()
    val isActive    = bool("is_active").default(true)
    val createdAt   = timestampWithTimeZone("created_at")
    val updatedAt   = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object Namespaces : Table("titlis_oltp.namespaces") {
    val id          = uuid("id").autoGenerate()
    val clusterId   = uuid("cluster_id").references(Clusters.id)
    val name        = varchar("name", 255)
    val isExcluded  = bool("is_excluded").default(false)
    val labels      = jsonb("labels").nullable()
    val annotations = jsonb("annotations").nullable()
    val createdAt   = timestampWithTimeZone("created_at")
    val updatedAt   = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object Workloads : Table("titlis_oltp.workloads") {
    val id                   = uuid("id").autoGenerate()
    val namespaceId          = uuid("namespace_id").references(Namespaces.id)
    val name                 = varchar("name", 255)
    val kind                 = varchar("kind", 100).default("Deployment")
    val serviceTier          = varchar("service_tier", 20).nullable()
    val ddGitRepositoryUrl   = text("dd_git_repository_url").nullable()
    val backstageComponent   = varchar("backstage_component", 255).nullable()
    val ownerTeam            = varchar("owner_team", 255).nullable()
    val labels               = jsonb("labels").nullable()
    val annotations          = jsonb("annotations").nullable()
    val resourceVersion      = varchar("resource_version", 100).nullable()
    val isActive             = bool("is_active").default(true)
    val createdAt            = timestampWithTimeZone("created_at")
    val updatedAt            = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object ValidationRules : Table("titlis_oltp.validation_rules") {
    val id                   = uuid("id").autoGenerate()
    val ruleId               = varchar("rule_id", 50)
    val pillar               = varchar("pillar", 50)
    val severity             = varchar("severity", 50)
    val ruleType             = varchar("rule_type", 50)
    val weight               = decimal("weight", 5, 2).default(1.0.toBigDecimal())
    val name                 = varchar("name", 255)
    val description          = text("description").nullable()
    val isRemediable         = bool("is_remediable").default(false)
    val remediationCategory  = varchar("remediation_category", 50).nullable()
    val isActive             = bool("is_active").default(true)
    val createdAt            = timestampWithTimeZone("created_at")
    val updatedAt            = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object AppScorecards : Table("titlis_oltp.app_scorecards") {
    val id                = uuid("id").autoGenerate()
    val workloadId        = uuid("workload_id").references(Workloads.id)
    val version           = integer("version").default(1)
    val overallScore      = decimal("overall_score", 5, 2)
    val complianceStatus  = varchar("compliance_status", 50).default("UNKNOWN")
    val totalRules        = integer("total_rules").default(0)
    val passedRules       = integer("passed_rules").default(0)
    val failedRules       = integer("failed_rules").default(0)
    val criticalFailures  = integer("critical_failures").default(0)
    val errorCount        = integer("error_count").default(0)
    val warningCount      = integer("warning_count").default(0)
    val evaluatedAt       = timestampWithTimeZone("evaluated_at")
    val k8sEventType      = varchar("k8s_event_type", 50).nullable()
    val rawMetadata       = jsonb("raw_metadata").nullable()
    val createdAt         = timestampWithTimeZone("created_at")
    val updatedAt         = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object PillarScores : Table("titlis_oltp.pillar_scores") {
    val id            = uuid("id").autoGenerate()
    val scorecardId   = uuid("scorecard_id").references(AppScorecards.id)
    val pillar        = varchar("pillar", 50)
    val score         = decimal("score", 5, 2)
    val passedChecks  = integer("passed_checks").default(0)
    val failedChecks  = integer("failed_checks").default(0)
    val weightedScore = decimal("weighted_score", 8, 4).nullable()
    val createdAt     = timestampWithTimeZone("created_at")
    val updatedAt     = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object AppRemediations : Table("titlis_oltp.app_remediations") {
    val id              = uuid("id").autoGenerate()
    val workloadId      = uuid("workload_id").references(Workloads.id)
    val version         = integer("version").default(1)
    val scorecardId     = uuid("scorecard_id").references(AppScorecards.id).nullable()
    val status          = varchar("status", 50).default("PENDING")
    val githubPrNumber  = integer("github_pr_number").nullable()
    val githubPrUrl     = text("github_pr_url").nullable()
    val githubPrTitle   = text("github_pr_title").nullable()
    val githubBranch    = text("github_branch").nullable()
    val repositoryUrl   = text("repository_url").nullable()
    val errorMessage    = text("error_message").nullable()
    val triggeredAt     = timestampWithTimeZone("triggered_at")
    val resolvedAt      = timestampWithTimeZone("resolved_at").nullable()
    val createdAt       = timestampWithTimeZone("created_at")
    val updatedAt       = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}

object SloConfigs : Table("titlis_oltp.slo_configs") {
    val id               = uuid("id").autoGenerate()
    val namespaceId      = uuid("namespace_id").references(Namespaces.id)
    val name             = varchar("name", 255)
    val sloType          = varchar("slo_type", 50)
    val timeframe        = varchar("timeframe", 10)
    val target           = decimal("target", 6, 4)
    val warning          = decimal("warning", 6, 4).nullable()
    val datadogSloId     = varchar("datadog_slo_id", 255).nullable()
    val datadogSloState  = varchar("datadog_slo_state", 50).nullable()
    val lastSyncAt       = timestampWithTimeZone("last_sync_at").nullable()
    val syncError        = text("sync_error").nullable()
    val specRaw          = jsonb("spec_raw").nullable()
    val version          = integer("version").default(1)
    val createdAt        = timestampWithTimeZone("created_at")
    val updatedAt        = timestampWithTimeZone("updated_at")
    override val primaryKey = PrimaryKey(id)
}
```

### 3.3 `database/tables/AuditTables.kt`

```kotlin
package io.titlis.api.database.tables

import org.jetbrains.exposed.sql.Table
import org.jetbrains.exposed.sql.kotlin.datetime.timestampWithTimeZone

object AppScorecardHistory : Table("titlis_audit.app_scorecard_history") {
    val id                = uuid("id").autoGenerate()
    val workloadId        = uuid("workload_id")
    val scorecardVersion  = integer("scorecard_version")
    val overallScore      = decimal("overall_score", 5, 2)
    val complianceStatus  = varchar("compliance_status", 50)
    val totalRules        = integer("total_rules")
    val passedRules       = integer("passed_rules")
    val failedRules       = integer("failed_rules")
    val criticalFailures  = integer("critical_failures")
    val errorCount        = integer("error_count")
    val warningCount      = integer("warning_count")
    val pillarScores      = jsonb("pillar_scores")
    val validationResults = jsonb("validation_results")
    val evaluatedAt       = timestampWithTimeZone("evaluated_at")
    val k8sEventType      = varchar("k8s_event_type", 50).nullable()
    val createdAt         = timestampWithTimeZone("created_at")
    override val primaryKey = PrimaryKey(id)
}

object NotificationLog : Table("titlis_audit.notification_log") {
    val id                = uuid("id").autoGenerate()
    val workloadId        = uuid("workload_id").nullable()
    val namespaceId       = uuid("namespace_id").nullable()
    val notificationType  = varchar("notification_type", 50)
    val severity          = varchar("severity", 50)
    val channel           = varchar("channel", 255).nullable()
    val title             = text("title").nullable()
    val messagePreview    = varchar("message_preview", 500).nullable()
    val sentAt            = timestampWithTimeZone("sent_at").nullable()
    val success           = bool("success").default(false)
    val errorMessage      = text("error_message").nullable()
    val createdAt         = timestampWithTimeZone("created_at")
    override val primaryKey = PrimaryKey(id)
}

object SloComplianceHistory : Table("titlis_audit.slo_compliance_history") {
    val id             = uuid("id").autoGenerate()
    val sloConfigId    = uuid("slo_config_id")
    val namespaceId    = uuid("namespace_id")
    val sloName        = varchar("slo_name", 255)
    val datadogSloId   = varchar("datadog_slo_id", 255).nullable()
    val sloType        = varchar("slo_type", 50)
    val timeframe      = varchar("timeframe", 10)
    val target         = decimal("target", 6, 4)
    val actualValue    = decimal("actual_value", 6, 4).nullable()
    val sloState       = varchar("slo_state", 50).nullable()
    val syncAction     = varchar("sync_action", 50).nullable()
    val syncError      = text("sync_error").nullable()
    val recordedAt     = timestampWithTimeZone("recorded_at")
    override val primaryKey = PrimaryKey(id)
}
```

### 3.4 `database/tables/TsTables.kt`

```kotlin
package io.titlis.api.database.tables

import org.jetbrains.exposed.sql.Table
import org.jetbrains.exposed.sql.kotlin.datetime.timestampWithTimeZone

object ResourceMetrics : Table("titlis_ts.resource_metrics") {
    val id                   = long("id").autoIncrement()
    val workloadId           = uuid("workload_id")
    val containerName        = varchar("container_name", 255).nullable()
    val metricSource         = varchar("metric_source", 50).default("datadog")
    val cpuAvgMillicores     = decimal("cpu_avg_millicores", 10, 3).nullable()
    val cpuP95Millicores     = decimal("cpu_p95_millicores", 10, 3).nullable()
    val memAvgMib            = decimal("mem_avg_mib", 10, 3).nullable()
    val memP95Mib            = decimal("mem_p95_mib", 10, 3).nullable()
    val suggestedCpuRequest  = varchar("suggested_cpu_request", 50).nullable()
    val suggestedCpuLimit    = varchar("suggested_cpu_limit", 50).nullable()
    val suggestedMemRequest  = varchar("suggested_mem_request", 50).nullable()
    val suggestedMemLimit    = varchar("suggested_mem_limit", 50).nullable()
    val sampleWindow         = varchar("sample_window", 20).nullable()
    val collectedAt          = timestampWithTimeZone("collected_at")
    override val primaryKey = PrimaryKey(id)
}

object ScorecardScores : Table("titlis_ts.scorecard_scores") {
    val id               = long("id").autoIncrement()
    val workloadId       = uuid("workload_id")
    val overallScore     = decimal("overall_score", 5, 2)
    val resilienceScore  = decimal("resilience_score", 5, 2).nullable()
    val securityScore    = decimal("security_score", 5, 2).nullable()
    val costScore        = decimal("cost_score", 5, 2).nullable()
    val performanceScore = decimal("performance_score", 5, 2).nullable()
    val operationalScore = decimal("operational_score", 5, 2).nullable()
    val complianceScore  = decimal("compliance_score", 5, 2).nullable()
    val complianceStatus = varchar("compliance_status", 50)
    val passedRules      = integer("passed_rules").nullable()
    val failedRules      = integer("failed_rules").nullable()
    val recordedAt       = timestampWithTimeZone("recorded_at")
    override val primaryKey = PrimaryKey(id)
}
```

---

## Passo 4 — Protocolo TitlisUDP

### 4.1 Especificação do protocolo

Cada datagrama UDP é um JSON UTF-8 com a estrutura:

```json
{
  "v": 1,
  "t": "<event_type>",
  "ts": 1710000000000,
  "data": { ... }
}
```

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `v` | int | Versão do protocolo (sempre `1` nesta versão) |
| `t` | string | Tipo do evento (ver tabela abaixo) |
| `ts` | long | Unix timestamp em milissegundos (UTC) |
| `data` | object | Payload específico do tipo de evento |

**Tipos de evento:**

| `t` | Origem no operador | Tabelas afetadas |
|-----|-------------------|------------------|
| `scorecard_evaluated` | `ScorecardController` | `app_scorecards`, `pillar_scores`, `ts.scorecard_scores` |
| `remediation_started` | `RemediationService` | `app_remediations` |
| `remediation_updated` | `RemediationService` | `app_remediations` |
| `slo_reconciled` | `SLOController` | `slo_configs`, `audit.slo_compliance_history` |
| `notification_sent` | `SlackService` | `audit.notification_log` |
| `resource_metrics` | `RemediationService` | `ts.resource_metrics` |

**Limite de payload:** 32KB por datagrama. Para scorecards completos (26 regras), o payload típico
é ~3–4KB. Nunca haverá fragmentação dentro do limite de MTU (1472 bytes safe) — usar a porta 8125
em rede interna do cluster (loopback ou pod network) sem risco de perda.

**Sem ACK:** O operador não aguarda resposta. Se o servidor estiver indisponível, o evento é
silenciosamente descartado (fire-and-forget). O CRD Kubernetes continua sendo a fonte de verdade
do operador.

### 4.2 `domain/Events.kt`

```kotlin
package io.titlis.api.domain

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class UdpEnvelope(
    val v: Int,
    val t: String,
    val ts: Long,
    val data: JsonObject,
)

@Serializable
data class ScorecardEvaluatedEvent(
    @SerialName("workload_id") val workloadId: String,
    val namespace: String,
    val workload: String,
    val cluster: String,
    @SerialName("k8s_event_type") val k8sEventType: String,
    @SerialName("overall_score") val overallScore: Double,
    @SerialName("compliance_status") val complianceStatus: String,
    @SerialName("total_rules") val totalRules: Int,
    @SerialName("passed_rules") val passedRules: Int,
    @SerialName("failed_rules") val failedRules: Int,
    @SerialName("critical_failures") val criticalFailures: Int,
    @SerialName("error_count") val errorCount: Int,
    @SerialName("warning_count") val warningCount: Int,
    @SerialName("scorecard_version") val scorecardVersion: Int,
    @SerialName("pillar_scores") val pillarScores: List<PillarScoreData>,
    @SerialName("evaluated_at") val evaluatedAt: String,
)

@Serializable
data class PillarScoreData(
    val pillar: String,
    val score: Double,
    @SerialName("passed_checks") val passedChecks: Int,
    @SerialName("failed_checks") val failedChecks: Int,
    @SerialName("weighted_score") val weightedScore: Double? = null,
)

@Serializable
data class RemediationEvent(
    @SerialName("workload_id") val workloadId: String,
    val namespace: String,
    val workload: String,
    val status: String,
    @SerialName("previous_status") val previousStatus: String? = null,
    val version: Int,
    @SerialName("github_pr_number") val githubPrNumber: Int? = null,
    @SerialName("github_pr_url") val githubPrUrl: String? = null,
    @SerialName("github_branch") val githubBranch: String? = null,
    @SerialName("repository_url") val repositoryUrl: String? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("triggered_at") val triggeredAt: String,
    @SerialName("resolved_at") val resolvedAt: String? = null,
)

@Serializable
data class SloReconciledEvent(
    @SerialName("slo_config_id") val sloConfigId: String,
    val namespace: String,
    @SerialName("slo_name") val sloName: String,
    @SerialName("slo_type") val sloType: String,
    val timeframe: String,
    val target: Double,
    val warning: Double? = null,
    @SerialName("datadog_slo_id") val datadogSloId: String? = null,
    @SerialName("datadog_slo_state") val datadogSloState: String? = null,
    @SerialName("sync_action") val syncAction: String,
    @SerialName("sync_error") val syncError: String? = null,
    @SerialName("actual_value") val actualValue: Double? = null,
)

@Serializable
data class NotificationSentEvent(
    @SerialName("workload_id") val workloadId: String? = null,
    @SerialName("namespace_id") val namespaceId: String? = null,
    val namespace: String,
    @SerialName("notification_type") val notificationType: String,
    val severity: String,
    val channel: String? = null,
    val title: String? = null,
    @SerialName("message_preview") val messagePreview: String? = null,
    val success: Boolean,
    @SerialName("error_message") val errorMessage: String? = null,
)

@Serializable
data class ResourceMetricsEvent(
    @SerialName("workload_id") val workloadId: String,
    val namespace: String,
    val workload: String,
    @SerialName("container_name") val containerName: String? = null,
    @SerialName("cpu_avg_millicores") val cpuAvgMillicores: Double? = null,
    @SerialName("cpu_p95_millicores") val cpuP95Millicores: Double? = null,
    @SerialName("mem_avg_mib") val memAvgMib: Double? = null,
    @SerialName("mem_p95_mib") val memP95Mib: Double? = null,
    @SerialName("suggested_cpu_request") val suggestedCpuRequest: String? = null,
    @SerialName("suggested_cpu_limit") val suggestedCpuLimit: String? = null,
    @SerialName("suggested_mem_request") val suggestedMemRequest: String? = null,
    @SerialName("suggested_mem_limit") val suggestedMemLimit: String? = null,
    @SerialName("sample_window") val sampleWindow: String? = null,
)
```

### 4.3 `udp/UdpServer.kt`

```kotlin
package io.titlis.api.udp

import io.titlis.api.config.UdpConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.slf4j.LoggerFactory
import java.net.DatagramPacket
import java.net.DatagramSocket

class UdpServer(
    private val config: UdpConfig,
    private val router: EventRouter,
) {
    private val logger = LoggerFactory.getLogger(UdpServer::class.java)
    private val queue = Channel<ByteArray>(config.queueSize)

    fun start(scope: CoroutineScope) {
        // Worker coroutines processam a fila
        repeat(config.workers) { workerId ->
            scope.launch(Dispatchers.IO) {
                logger.info("UDP worker $workerId started")
                for (payload in queue) {
                    runCatching { router.route(payload) }
                        .onFailure { logger.warn("UDP route error: ${it.message}") }
                }
            }
        }

        // Receptor UDP em thread dedicada
        scope.launch(Dispatchers.IO) {
            DatagramSocket(config.port).use { socket ->
                logger.info("UDP server listening on port ${config.port}")
                val buffer = ByteArray(config.bufferSize)
                val packet = DatagramPacket(buffer, buffer.size)
                while (isActive) {
                    runCatching {
                        socket.receive(packet)
                        val payload = packet.data.copyOf(packet.length)
                        if (!queue.trySend(payload).isSuccess) {
                            logger.warn("UDP queue full — dropping event")
                        }
                    }.onFailure {
                        if (isActive) logger.warn("UDP receive error: ${it.message}")
                    }
                }
            }
        }
    }
}
```

### 4.4 `udp/EventRouter.kt`

```kotlin
package io.titlis.api.udp

import io.titlis.api.domain.*
import io.titlis.api.repository.MetricsRepository
import io.titlis.api.repository.RemediationRepository
import io.titlis.api.repository.ScorecardRepository
import io.titlis.api.repository.SloRepository
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement
import org.slf4j.LoggerFactory

class EventRouter(
    private val scorecardRepo: ScorecardRepository,
    private val remediationRepo: RemediationRepository,
    private val sloRepo: SloRepository,
    private val metricsRepo: MetricsRepository,
) {
    private val logger = LoggerFactory.getLogger(EventRouter::class.java)
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun route(payload: ByteArray) {
        val raw = payload.decodeToString()
        val envelope = runCatching { json.decodeFromString<UdpEnvelope>(raw) }
            .getOrElse {
                logger.warn("Invalid UDP envelope: ${raw.take(200)}")
                return
            }

        if (envelope.v != 1) {
            logger.warn("Unsupported protocol version ${envelope.v}")
            return
        }

        when (envelope.t) {
            "scorecard_evaluated" -> {
                val event = json.decodeFromJsonElement<ScorecardEvaluatedEvent>(envelope.data)
                scorecardRepo.upsertScorecard(event)
            }
            "remediation_started", "remediation_updated" -> {
                val event = json.decodeFromJsonElement<RemediationEvent>(envelope.data)
                remediationRepo.upsertRemediation(event)
            }
            "slo_reconciled" -> {
                val event = json.decodeFromJsonElement<SloReconciledEvent>(envelope.data)
                sloRepo.upsertSloConfig(event)
            }
            "notification_sent" -> {
                val event = json.decodeFromJsonElement<NotificationSentEvent>(envelope.data)
                scorecardRepo.insertNotificationLog(event)
            }
            "resource_metrics" -> {
                val event = json.decodeFromJsonElement<ResourceMetricsEvent>(envelope.data)
                metricsRepo.insertResourceMetrics(event)
            }
            else -> logger.warn("Unknown event type: ${envelope.t}")
        }
    }
}
```

---

## Passo 5 — Repositórios

### 5.1 `repository/ScorecardRepository.kt`

```kotlin
package io.titlis.api.repository

import io.titlis.api.database.DatabaseFactory.dbQuery
import io.titlis.api.database.tables.*
import io.titlis.api.domain.NotificationSentEvent
import io.titlis.api.domain.ScorecardEvaluatedEvent
import kotlinx.datetime.toKotlinInstant
import org.jetbrains.exposed.sql.*
import org.jetbrains.exposed.sql.upsert
import java.time.Instant
import java.util.UUID

class ScorecardRepository {

    suspend fun upsertScorecard(event: ScorecardEvaluatedEvent) = dbQuery {
        val workloadId = UUID.fromString(event.workloadId)
        val now = Instant.now().toKotlinInstant()

        // Upsert scorecard atual (SCD Type 4 — trigger DB faz o histórico)
        AppScorecards.upsert(AppScorecards.workloadId) {
            it[AppScorecards.workloadId] = workloadId
            it[version] = event.scorecardVersion
            it[overallScore] = event.overallScore.toBigDecimal()
            it[complianceStatus] = event.complianceStatus
            it[totalRules] = event.totalRules
            it[passedRules] = event.passedRules
            it[failedRules] = event.failedRules
            it[criticalFailures] = event.criticalFailures
            it[errorCount] = event.errorCount
            it[warningCount] = event.warningCount
            it[evaluatedAt] = Instant.parse(event.evaluatedAt).toKotlinInstant()
            it[k8sEventType] = event.k8sEventType
            it[updatedAt] = now
        }

        // Obter scorecard_id recém-upsertado
        val scorecardId = AppScorecards
            .select(AppScorecards.id)
            .where { AppScorecards.workloadId eq workloadId }
            .single()[AppScorecards.id]

        // Substituir pillar_scores (delete + insert — scorecard atual tem CASCADE)
        PillarScores.deleteWhere { PillarScores.scorecardId eq scorecardId }
        event.pillarScores.forEach { ps ->
            PillarScores.insert {
                it[PillarScores.scorecardId] = scorecardId
                it[pillar] = ps.pillar
                it[score] = ps.score.toBigDecimal()
                it[passedChecks] = ps.passedChecks
                it[failedChecks] = ps.failedChecks
                it[weightedScore] = ps.weightedScore?.toBigDecimal()
                it[createdAt] = now
                it[updatedAt] = now
            }
        }

        // Inserir na série temporal (append-only)
        ScorecardScores.insert {
            it[ScorecardScores.workloadId] = workloadId
            it[overallScore] = event.overallScore.toBigDecimal()
            it[complianceStatus] = event.complianceStatus
            it[passedRules] = event.passedRules
            it[failedRules] = event.failedRules
            it[recordedAt] = now
            event.pillarScores.forEach { ps ->
                when (ps.pillar) {
                    "RESILIENCE"   -> it[resilienceScore] = ps.score.toBigDecimal()
                    "SECURITY"     -> it[securityScore] = ps.score.toBigDecimal()
                    "COST"         -> it[costScore] = ps.score.toBigDecimal()
                    "PERFORMANCE"  -> it[performanceScore] = ps.score.toBigDecimal()
                    "OPERATIONAL"  -> it[operationalScore] = ps.score.toBigDecimal()
                    "COMPLIANCE"   -> it[complianceScore] = ps.score.toBigDecimal()
                }
            }
        }
    }

    suspend fun insertNotificationLog(event: NotificationSentEvent) = dbQuery {
        NotificationLog.insert {
            it[workloadId] = event.workloadId?.let { id -> UUID.fromString(id) }
            it[namespaceId] = event.namespaceId?.let { id -> UUID.fromString(id) }
            it[notificationType] = event.notificationType
            it[severity] = event.severity
            it[channel] = event.channel
            it[title] = event.title
            it[messagePreview] = event.messagePreview?.take(500)
            it[success] = event.success
            it[errorMessage] = event.errorMessage
            it[createdAt] = Instant.now().toKotlinInstant()
        }
    }

    suspend fun getDashboard(clusterName: String? = null): List<Map<String, Any?>> = dbQuery {
        val query = (Workloads innerJoin Namespaces innerJoin Clusters)
            .leftJoin(AppScorecards, { Workloads.id }, { AppScorecards.workloadId })
            .leftJoin(AppRemediations, { Workloads.id }, { AppRemediations.workloadId })
            .select(
                Workloads.id, Clusters.name, Clusters.environment, Namespaces.name,
                Workloads.name, Workloads.kind, Workloads.serviceTier, Workloads.ownerTeam,
                AppScorecards.overallScore, AppScorecards.complianceStatus,
                AppScorecards.passedRules, AppScorecards.failedRules,
                AppScorecards.criticalFailures, AppScorecards.version,
                AppScorecards.evaluatedAt, AppRemediations.status,
                AppRemediations.githubPrUrl, AppRemediations.githubPrNumber,
            )
            .where {
                (Workloads.isActive eq true) and (Namespaces.isExcluded eq false)
            }

        if (clusterName != null) {
            query.andWhere { Clusters.name eq clusterName }
        }

        query.map { row ->
            mapOf(
                "workload_id" to row[Workloads.id].toString(),
                "cluster" to row[Clusters.name],
                "environment" to row[Clusters.environment],
                "namespace" to row[Namespaces.name],
                "workload" to row[Workloads.name],
                "overall_score" to row[AppScorecards.overallScore],
                "compliance_status" to row[AppScorecards.complianceStatus],
                "remediation_status" to row[AppRemediations.status],
                "github_pr_url" to row[AppRemediations.githubPrUrl],
            )
        }
    }
}
```

### 5.2 `repository/RemediationRepository.kt`

```kotlin
package io.titlis.api.repository

import io.titlis.api.database.DatabaseFactory.dbQuery
import io.titlis.api.database.tables.AppRemediations
import io.titlis.api.domain.RemediationEvent
import kotlinx.datetime.toKotlinInstant
import org.jetbrains.exposed.sql.select
import org.jetbrains.exposed.sql.upsert
import java.time.Instant
import java.util.UUID

class RemediationRepository {

    suspend fun upsertRemediation(event: RemediationEvent) = dbQuery {
        val workloadId = UUID.fromString(event.workloadId)
        AppRemediations.upsert(AppRemediations.workloadId) {
            it[AppRemediations.workloadId] = workloadId
            it[version] = event.version
            it[status] = event.status
            it[githubPrNumber] = event.githubPrNumber
            it[githubPrUrl] = event.githubPrUrl
            it[githubBranch] = event.githubBranch
            it[repositoryUrl] = event.repositoryUrl
            it[errorMessage] = event.errorMessage
            it[triggeredAt] = Instant.parse(event.triggeredAt).toKotlinInstant()
            it[resolvedAt] = event.resolvedAt?.let { ts -> Instant.parse(ts).toKotlinInstant() }
            it[updatedAt] = Instant.now().toKotlinInstant()
        }
    }

    suspend fun getByWorkload(workloadId: UUID): Map<String, Any?>? = dbQuery {
        AppRemediations
            .select(AppRemediations.columns)
            .where { AppRemediations.workloadId eq workloadId }
            .singleOrNull()
            ?.let { row ->
                mapOf(
                    "status" to row[AppRemediations.status],
                    "version" to row[AppRemediations.version],
                    "github_pr_url" to row[AppRemediations.githubPrUrl],
                    "github_pr_number" to row[AppRemediations.githubPrNumber],
                    "triggered_at" to row[AppRemediations.triggeredAt].toString(),
                )
            }
    }
}
```

---

## Passo 6 — API REST (Ktor Routes)

### 6.1 `routes/ScorecardRoutes.kt`

```kotlin
package io.titlis.api.routes

import io.ktor.http.*
import io.ktor.server.application.*
import io.ktor.server.response.*
import io.ktor.server.routing.*
import io.titlis.api.repository.ScorecardRepository

fun Application.scorecardRoutes(repo: ScorecardRepository) {
    routing {
        route("/v1") {
            // Dashboard: estado atual de todos os workloads
            get("/dashboard") {
                val cluster = call.request.queryParameters["cluster"]
                call.respond(repo.getDashboard(cluster))
            }

            // Estado atual de um workload específico
            get("/workloads/{workloadId}/scorecard") {
                val id = call.parameters["workloadId"]
                    ?: return@get call.respond(HttpStatusCode.BadRequest, "workloadId required")
                // implementar getByWorkloadId no repo
                call.respond(mapOf("workload_id" to id))
            }
        }
    }
}
```

### 6.2 `routes/RemediationRoutes.kt`

```kotlin
package io.titlis.api.routes

import io.ktor.http.*
import io.ktor.server.application.*
import io.ktor.server.response.*
import io.ktor.server.routing.*
import io.titlis.api.repository.RemediationRepository
import java.util.UUID

fun Application.remediationRoutes(repo: RemediationRepository) {
    routing {
        route("/v1") {
            get("/workloads/{workloadId}/remediation") {
                val idStr = call.parameters["workloadId"]
                    ?: return@get call.respond(HttpStatusCode.BadRequest, "workloadId required")
                val id = runCatching { UUID.fromString(idStr) }
                    .getOrElse { return@get call.respond(HttpStatusCode.BadRequest, "invalid UUID") }
                val result = repo.getByWorkload(id)
                    ?: return@get call.respond(HttpStatusCode.NotFound)
                call.respond(result)
            }
        }
    }
}
```

### 6.3 `routes/HealthRoutes.kt`

```kotlin
package io.titlis.api.routes

import io.ktor.server.application.*
import io.ktor.server.response.*
import io.ktor.server.routing.*

fun Application.healthRoutes() {
    routing {
        get("/health") {
            call.respond(mapOf("status" to "ok", "service" to "titlis-api"))
        }
        get("/ready") {
            // Verificar conexão com DB antes de responder 200
            call.respond(mapOf("status" to "ready"))
        }
    }
}
```

### 6.4 `Main.kt`

```kotlin
package io.titlis.api

import io.ktor.serialization.kotlinx.json.*
import io.ktor.server.application.*
import io.ktor.server.engine.*
import io.ktor.server.netty.*
import io.ktor.server.plugins.contentnegotiation.*
import io.ktor.server.plugins.statuspages.*
import io.ktor.server.response.*
import io.titlis.api.config.AppConfig
import io.titlis.api.database.DatabaseFactory
import io.titlis.api.repository.MetricsRepository
import io.titlis.api.repository.RemediationRepository
import io.titlis.api.repository.ScorecardRepository
import io.titlis.api.repository.SloRepository
import io.titlis.api.routes.healthRoutes
import io.titlis.api.routes.remediationRoutes
import io.titlis.api.routes.scorecardRoutes
import io.titlis.api.udp.EventRouter
import io.titlis.api.udp.UdpServer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.serialization.json.Json

fun main() {
    embeddedServer(Netty, port = 8080, module = Application::module).start(wait = true)
}

fun Application.module() {
    val config = AppConfig.from(environment.config)

    DatabaseFactory.init(config.database)

    val scorecardRepo   = ScorecardRepository()
    val remediationRepo = RemediationRepository()
    val sloRepo         = SloRepository()
    val metricsRepo     = MetricsRepository()

    val router    = EventRouter(scorecardRepo, remediationRepo, sloRepo, metricsRepo)
    val udpServer = UdpServer(config.udp, router)

    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    udpServer.start(scope)

    install(ContentNegotiation) {
        json(Json { ignoreUnknownKeys = true; prettyPrint = false })
    }

    install(StatusPages) {
        exception<Throwable> { call, cause ->
            call.respond(
                io.ktor.http.HttpStatusCode.InternalServerError,
                mapOf("error" to cause.message)
            )
        }
    }

    healthRoutes()
    scorecardRoutes(scorecardRepo)
    remediationRoutes(remediationRepo)
}
```

---

## Passo 7 — Deploy no Kubernetes

### 7.1 Adicionar `titlis-api` ao Helm chart existente

Criar `charts/titlis-operator/templates/titlis-api-deployment.yaml`:

```yaml
{{- if .Values.titlisApi.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: titlis-api
  namespace: {{ .Release.Namespace }}
  labels:
    app: titlis-api
    app.kubernetes.io/part-of: titlis-operator
spec:
  replicas: {{ .Values.titlisApi.replicas | default 2 }}
  selector:
    matchLabels:
      app: titlis-api
  template:
    metadata:
      labels:
        app: titlis-api
    spec:
      containers:
        - name: titlis-api
          image: {{ .Values.titlisApi.image }}
          ports:
            - containerPort: 8080
              name: http
              protocol: TCP
            - containerPort: 8125
              name: titlis-udp
              protocol: UDP
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: titlis-db-secret
                  key: url
            - name: DATABASE_USER
              valueFrom:
                secretKeyRef:
                  name: titlis-db-secret
                  key: username
            - name: DATABASE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: titlis-db-secret
                  key: password
            - name: TITLIS_UDP_PORT
              value: "8125"
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 15
          readinessProbe:
            httpGet:
              path: /ready
              port: 8080
            initialDelaySeconds: 10
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
{{- end }}
```

### 7.2 Service (HTTP + UDP)

Criar `charts/titlis-operator/templates/titlis-api-service.yaml`:

```yaml
{{- if .Values.titlisApi.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: titlis-api
  namespace: {{ .Release.Namespace }}
spec:
  selector:
    app: titlis-api
  ports:
    - name: http
      port: 8080
      targetPort: 8080
      protocol: TCP
    - name: titlis-udp
      port: 8125
      targetPort: 8125
      protocol: UDP
  type: ClusterIP
{{- end }}
```

### 7.3 Secret do banco

```bash
kubectl create secret generic titlis-db-secret \
  --namespace titlis-system \
  --from-literal=url="jdbc:postgresql://postgres-svc:5432/titlis" \
  --from-literal=username="titlis_operator" \
  --from-literal=password="<senha>"
```

### 7.4 Adicionar ao `values.yaml`

```yaml
titlisApi:
  enabled: true
  image: "your-registry/titlis-api:0.1.0"
  replicas: 2
```

---

## Passo 8 — Integração no Operator Python

### 8.1 Port interface — `src/application/ports/titlis_api_port.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class RemediationState:
    status: str
    version: int
    github_pr_url: Optional[str]
    github_pr_number: Optional[int]


class TitlisApiPort(ABC):

    @abstractmethod
    async def send_scorecard_evaluated(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_remediation_event(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_slo_reconciled(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_notification_log(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_resource_metrics(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def get_remediation(self, workload_id: str) -> Optional[RemediationState]:
        ...
```

### 8.2 Adapter UDP — `src/infrastructure/titlis_api/udp_client.py`

```python
import asyncio
import json
import time
import logging

from src.application.ports.titlis_api_port import TitlisApiPort, RemediationState
from typing import Optional

logger = logging.getLogger(__name__)


class TitlisApiUdpClient(TitlisApiPort):

    def __init__(self, host: str, udp_port: int, http_base_url: str):
        self._host = host
        self._udp_port = udp_port
        self._http_base_url = http_base_url
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def _ensure_socket(self):
        if self._transport is None or self._transport.is_closing():
            self._loop = asyncio.get_event_loop()
            self._transport, _ = await self._loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                remote_addr=(self._host, self._udp_port),
            )

    async def _send_udp(self, event_type: str, data: dict) -> None:
        envelope = {
            "v": 1,
            "t": event_type,
            "ts": int(time.time() * 1000),
            "data": data,
        }
        try:
            await self._ensure_socket()
            payload = json.dumps(envelope, default=str).encode("utf-8")
            self._transport.sendto(payload)
        except Exception as exc:
            logger.warning(
                "titlis_api_udp_send_failed",
                extra={"event": event_type, "error": str(exc)},
            )

    async def send_scorecard_evaluated(self, payload: dict) -> None:
        await self._send_udp("scorecard_evaluated", payload)

    async def send_remediation_event(self, payload: dict) -> None:
        await self._send_udp("remediation_updated", payload)

    async def send_slo_reconciled(self, payload: dict) -> None:
        await self._send_udp("slo_reconciled", payload)

    async def send_notification_log(self, payload: dict) -> None:
        await self._send_udp("notification_sent", payload)

    async def send_resource_metrics(self, payload: dict) -> None:
        await self._send_udp("resource_metrics", payload)

    async def get_remediation(self, workload_id: str) -> Optional[RemediationState]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._http_base_url}/v1/workloads/{workload_id}/remediation"
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
                return RemediationState(
                    status=data["status"],
                    version=data["version"],
                    github_pr_url=data.get("github_pr_url"),
                    github_pr_number=data.get("github_pr_number"),
                )
        except Exception as exc:
            logger.warning(
                "titlis_api_http_get_failed",
                extra={"workload_id": workload_id, "error": str(exc)},
            )
            return None

    async def close(self) -> None:
        if self._transport and not self._transport.is_closing():
            self._transport.close()
```

### 8.3 Novas ENV vars — adicionar em `src/settings.py`

```python
class TitlisApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TITLIS_API_")

    enabled: bool = Field(default=False)
    host: str = Field(default="titlis-api.titlis-system.svc.cluster.local")
    udp_port: int = Field(default=8125)
    http_port: int = Field(default=8080)

    @property
    def http_base_url(self) -> str:
        return f"http://{self.host}:{self.http_port}"
```

Adicionar ao `Settings` principal:

```python
class Settings(BaseSettings):
    # ... campos existentes ...
    titlis_api: TitlisApiSettings = Field(default_factory=TitlisApiSettings)
```

### 8.4 Bootstrap — `src/bootstrap/dependencies.py`

```python
from functools import lru_cache
from src.settings import settings

@lru_cache()
def get_titlis_api_client():
    if not settings.titlis_api.enabled:
        return None
    from src.infrastructure.titlis_api.udp_client import TitlisApiUdpClient
    return TitlisApiUdpClient(
        host=settings.titlis_api.host,
        udp_port=settings.titlis_api.udp_port,
        http_base_url=settings.titlis_api.http_base_url,
    )
```

### 8.5 Enviar eventos no `ScorecardController`

No método `on_resource_event` do `ScorecardController`, após gravar o CRD:

```python
titlis_client = get_titlis_api_client()
if titlis_client is not None:
    await titlis_client.send_scorecard_evaluated({
        "workload_id": str(workload_uid),
        "namespace": namespace,
        "workload": name,
        "cluster": settings.kubernetes_namespace,
        "k8s_event_type": event_type,
        "overall_score": scorecard.overall_score,
        "compliance_status": scorecard.compliance_status.value,
        "total_rules": scorecard.total_rules,
        "passed_rules": scorecard.passed_rules,
        "failed_rules": scorecard.failed_rules,
        "critical_failures": scorecard.critical_failures,
        "error_count": scorecard.error_count,
        "warning_count": scorecard.warning_count,
        "scorecard_version": scorecard.version,
        "pillar_scores": [
            {
                "pillar": ps.pillar.value,
                "score": ps.score,
                "passed_checks": ps.passed_checks,
                "failed_checks": ps.failed_checks,
                "weighted_score": ps.weighted_score,
            }
            for ps in scorecard.pillar_scores.values()
        ],
        "evaluated_at": scorecard.evaluated_at.isoformat(),
    })
```

### 8.6 Enviar eventos no `RemediationService`

Após `create_remediation_pr` retornar `RemediationResult`:

```python
if titlis_client is not None:
    await titlis_client.send_remediation_event({
        "workload_id": str(resource_uid),
        "namespace": namespace,
        "workload": resource_name,
        "status": "PR_OPEN" if result.success else "FAILED",
        "version": 1,
        "github_pr_number": result.pull_request.number if result.pull_request else None,
        "github_pr_url": result.pull_request.url if result.pull_request else None,
        "github_branch": result.pull_request.branch if result.pull_request else None,
        "error_message": result.error,
        "triggered_at": datetime.utcnow().isoformat() + "Z",
    })
```

---

## Passo 9 — Testes

### 9.1 Teste do servidor UDP

```kotlin
// src/test/kotlin/io/titlis/api/udp/UdpServerTest.kt
package io.titlis.api.udp

import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Test
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

class UdpServerTest {

    @Test
    fun `scorecard_evaluated event is routed correctly`() = runTest {
        val router = mockk<EventRouter>(relaxed = true)
        // Iniciar servidor em porta aleatória para teste
        val payload = """
            {"v":1,"t":"scorecard_evaluated","ts":1710000000000,"data":{
              "workload_id":"123e4567-e89b-12d3-a456-426614174000",
              "namespace":"production","workload":"api","cluster":"prod",
              "k8s_event_type":"update","overall_score":85.0,
              "compliance_status":"COMPLIANT","total_rules":26,
              "passed_rules":22,"failed_rules":4,"critical_failures":0,
              "error_count":1,"warning_count":3,"scorecard_version":5,
              "pillar_scores":[],"evaluated_at":"2026-03-16T10:00:00Z"
            }}
        """.trimIndent()

        // Enviar via UDP
        val socket = DatagramSocket()
        val bytes = payload.toByteArray()
        socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getLoopbackAddress(), 18125))
        socket.close()

        // Verificar que router.route foi chamado com o payload
        // (teste simplificado — verificar com delay ou Channel em testes reais)
    }
}
```

### 9.2 Teste de rotas REST

```kotlin
// src/test/kotlin/io/titlis/api/routes/ScorecardRoutesTest.kt
package io.titlis.api.routes

import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import io.ktor.server.testing.*
import io.mockk.coEvery
import io.mockk.mockk
import io.titlis.api.repository.ScorecardRepository
import kotlin.test.Test
import kotlin.test.assertEquals

class ScorecardRoutesTest {

    @Test
    fun `GET dashboard returns 200`() = testApplication {
        val repo = mockk<ScorecardRepository>()
        coEvery { repo.getDashboard(any()) } returns listOf(
            mapOf("workload_id" to "abc", "overall_score" to 90.0)
        )
        application {
            scorecardRoutes(repo)
        }
        val response = client.get("/v1/dashboard")
        assertEquals(HttpStatusCode.OK, response.status)
    }
}
```

### 9.3 Testes no operador Python

```python
# tests/unit/test_titlis_api_client.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.infrastructure.titlis_api.udp_client import TitlisApiUdpClient


@pytest.mark.asyncio
async def test_send_scorecard_evaluated_sends_udp():
    client = TitlisApiUdpClient(
        host="localhost",
        udp_port=8125,
        http_base_url="http://localhost:8080",
    )
    mock_transport = MagicMock()
    client._transport = mock_transport

    await client.send_scorecard_evaluated({"namespace": "prod", "workload": "api"})

    mock_transport.sendto.assert_called_once()
    raw = mock_transport.sendto.call_args[0][0]
    envelope = json.loads(raw.decode())
    assert envelope["v"] == 1
    assert envelope["t"] == "scorecard_evaluated"
    assert "ts" in envelope


@pytest.mark.asyncio
async def test_get_remediation_returns_none_on_404():
    client = TitlisApiUdpClient("localhost", 8125, "http://localhost:8080")
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status_code = 404
        mock_get.return_value.__aenter__.return_value.get.return_value = mock_resp
        result = await client.get_remediation("some-uuid")
        assert result is None
```

---

## Passo 10 — Novas ENV vars a adicionar no `CLAUDE.md` (Seção 3)

```bash
# Titlis API — comunicação operator → banco
TITLIS_API_ENABLED=false                                                  # feature flag
TITLIS_API_HOST=titlis-api.titlis-system.svc.cluster.local                # hostname do serviço
TITLIS_API_UDP_PORT=8125                                                   # porta TitlisUDP
TITLIS_API_HTTP_PORT=8080                                                  # porta REST

# Titlis API — banco de dados (configurado na API, não no operador)
DATABASE_URL=jdbc:postgresql://postgres:5432/titlis
DATABASE_USER=titlis_operator
DATABASE_PASSWORD=<secret>
DB_POOL_MAX=10
```

---

## Checklist de Integração

Execute em ordem ao integrar ao operador existente:

### API Kotlin

- [ ] `./gradlew build` — compilação sem erros
- [ ] `./gradlew test` — todos os testes passam
- [ ] `docker build -t titlis-api:0.1.0 .` — imagem gerada
- [ ] Servidor UDP responde na porta 8125 (testar com `nc -u localhost 8125`)
- [ ] `GET /health` retorna `{"status":"ok"}`
- [ ] `GET /ready` retorna 200 com banco conectado
- [ ] `GET /v1/dashboard` retorna array (vazio se sem dados)

### Operator Python

- [ ] `TITLIS_API_ENABLED=true` adicionado ao env
- [ ] `make lint && make test-unit` passam após adicionar `TitlisApiSettings` em `settings.py`
- [ ] `TitlisApiUdpClient` adicionado como Port + Adapter (regra RA-01)
- [ ] `get_titlis_api_client()` adicionado a `dependencies.py` (regra RA-02)
- [ ] Feature flag verificada antes de usar: `if titlis_client is not None` (regra R-08)
- [ ] Envio UDP em `ScorecardController` após escrita do CRD (não antes)
- [ ] Envio UDP em `RemediationService` após `create_remediation_pr`
- [ ] Envio UDP em `SLOController` após `reconcile_slo`
- [ ] Envio UDP em `SlackService` após `send_notification`
- [ ] Novas ENV vars documentadas no `CLAUDE.md` seção 3

### Kubernetes

- [ ] Secret `titlis-db-secret` criado no namespace `titlis-system`
- [ ] `helm upgrade` com `titlisApi.enabled=true`
- [ ] Pod `titlis-api` em Running
- [ ] `kubectl logs titlis-api` sem erros de DB connection
- [ ] Operator enviando eventos UDP (verificar em `kubectl logs titlis-api | grep scorecard_evaluated`)

### Banco de dados

- [ ] `psql -d titlis -f db/schema.sql` sem erros
- [ ] Triggers funcionando: `UPDATE app_scorecards SET version=2 ...` → linha criada em `app_scorecard_history`
- [ ] View `titlis_oltp.v_workload_dashboard` retorna dados após primeiro evento

---

## Referências Cruzadas

| Documento | Relação |
|-----------|---------|
| [db/schema.sql](../db/schema.sql) | DDL completo que a API usa |
| [docs/modelagem-dados.md](modelagem-dados.md) | Justificativas arquiteturais do schema |
| [docs/evolution-checklist.md](evolution-checklist.md) | Esta implementação cobre Fase 1 Semanas 3–4 |
| [CLAUDE.md §3](../CLAUDE.md) | ENV vars novas a adicionar |
| [CLAUDE.md §13](../CLAUDE.md) | Status do banco e comando de criação |
