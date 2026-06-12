from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "log_analyses" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "analysis_date" DATE NOT NULL UNIQUE,
    "mcp_artifact" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "failure_stage" VARCHAR(80),
    "log_window_since" TIMESTAMPTZ,
    "log_window_until" TIMESTAMPTZ,
    "mcp_collect_logs_id" VARCHAR(255),
    "summary" TEXT NOT NULL,
    "severity" VARCHAR(10) NOT NULL DEFAULT 'INFO',
    "key_findings" JSONB NOT NULL,
    "recommendations" TEXT NOT NULL,
    "trend_summary" TEXT NOT NULL,
    "execution_time_seconds" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "gpt_tokens_used" INT NOT NULL DEFAULT 0,
    "gpt_cost_usd" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "email_sent" BOOL NOT NULL DEFAULT False,
    "error_message" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_log_analyse_created_d55308" ON "log_analyses" ("created_at");
CREATE INDEX IF NOT EXISTS "idx_log_analyse_analysi_d587d2" ON "log_analyses" ("analysis_date");
CREATE INDEX IF NOT EXISTS "idx_log_analyse_status_bb4643" ON "log_analyses" ("status");
CREATE INDEX IF NOT EXISTS "idx_log_analyse_severit_bd9a2c" ON "log_analyses" ("severity");
CREATE INDEX IF NOT EXISTS "idx_log_analyse_email_s_d8b63e" ON "log_analyses" ("email_sent");
COMMENT ON COLUMN "log_analyses"."id" IS 'Database-generated integer id for this log analysis.';
COMMENT ON COLUMN "log_analyses"."created_at" IS 'UTC timestamp when this log analysis row was created.';
COMMENT ON COLUMN "log_analyses"."analysis_date" IS 'Calendar date this log analysis represents.';
COMMENT ON COLUMN "log_analyses"."mcp_artifact" IS 'Opaque collect_logs artifact payload returned by MCP.';
COMMENT ON COLUMN "log_analyses"."status" IS 'Execution status for this log analysis job.';
COMMENT ON COLUMN "log_analyses"."started_at" IS 'UTC timestamp when this log analysis job started.';
COMMENT ON COLUMN "log_analyses"."finished_at" IS 'UTC timestamp when this log analysis job finished.';
COMMENT ON COLUMN "log_analyses"."failure_stage" IS 'Pipeline stage where this log analysis failed, if any.';
COMMENT ON COLUMN "log_analyses"."log_window_since" IS 'Start of the log collection time window requested from MCP.';
COMMENT ON COLUMN "log_analyses"."log_window_until" IS 'End of the log collection time window requested from MCP.';
COMMENT ON COLUMN "log_analyses"."mcp_collect_logs_id" IS 'Stable MCP collect_logs artifact id when MCP returns one.';
COMMENT ON COLUMN "log_analyses"."summary" IS 'LLM-generated log analysis summary.';
COMMENT ON COLUMN "log_analyses"."severity" IS 'LLM-classified severity for this log analysis.';
COMMENT ON COLUMN "log_analyses"."key_findings" IS 'List of important findings extracted from the log analysis.';
COMMENT ON COLUMN "log_analyses"."recommendations" IS 'LLM-generated operational recommendations.';
COMMENT ON COLUMN "log_analyses"."trend_summary" IS 'LLM-generated trend comparison against prior analyses.';
COMMENT ON COLUMN "log_analyses"."execution_time_seconds" IS 'Total wall-clock execution time for this log analysis job.';
COMMENT ON COLUMN "log_analyses"."gpt_tokens_used" IS 'OpenAI token count used for this log analysis.';
COMMENT ON COLUMN "log_analyses"."gpt_cost_usd" IS 'Estimated OpenAI API cost in USD for this log analysis.';
COMMENT ON COLUMN "log_analyses"."email_sent" IS 'Whether the log analysis email was sent.';
COMMENT ON COLUMN "log_analyses"."error_message" IS 'Error message captured when this log analysis failed.';
COMMENT ON TABLE "log_analyses" IS 'Stored log-analysis report and execution state.';
CREATE TABLE IF NOT EXISTS "sitemap_analyses" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "analysis_date" DATE NOT NULL UNIQUE,
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "failure_stage" VARCHAR(80),
    "fetch_duration_seconds" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "root_sitemap_url" VARCHAR(2048) NOT NULL,
    "total_sitemaps" INT NOT NULL DEFAULT 0,
    "total_urls" INT NOT NULL DEFAULT 0,
    "issue_summary" JSONB NOT NULL,
    "issues" JSONB NOT NULL,
    "summary" TEXT NOT NULL,
    "severity" VARCHAR(10) NOT NULL DEFAULT 'INFO',
    "key_findings" JSONB NOT NULL,
    "recommendations" TEXT NOT NULL,
    "trend_summary" TEXT NOT NULL,
    "execution_time_seconds" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "gpt_tokens_used" INT NOT NULL DEFAULT 0,
    "gpt_cost_usd" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "email_sent" BOOL NOT NULL DEFAULT False,
    "error_message" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_sitemap_ana_created_84ba90" ON "sitemap_analyses" ("created_at");
CREATE INDEX IF NOT EXISTS "idx_sitemap_ana_analysi_83638a" ON "sitemap_analyses" ("analysis_date");
CREATE INDEX IF NOT EXISTS "idx_sitemap_ana_status_f546b0" ON "sitemap_analyses" ("status");
CREATE INDEX IF NOT EXISTS "idx_sitemap_ana_severit_9baef6" ON "sitemap_analyses" ("severity");
CREATE INDEX IF NOT EXISTS "idx_sitemap_ana_email_s_b1552e" ON "sitemap_analyses" ("email_sent");
COMMENT ON COLUMN "sitemap_analyses"."id" IS 'Database-generated integer id for this sitemap analysis.';
COMMENT ON COLUMN "sitemap_analyses"."created_at" IS 'UTC timestamp when this sitemap analysis row was created.';
COMMENT ON COLUMN "sitemap_analyses"."analysis_date" IS 'Calendar date this sitemap analysis represents.';
COMMENT ON COLUMN "sitemap_analyses"."status" IS 'Execution status for this sitemap analysis job.';
COMMENT ON COLUMN "sitemap_analyses"."started_at" IS 'UTC timestamp when this sitemap analysis job started.';
COMMENT ON COLUMN "sitemap_analyses"."finished_at" IS 'UTC timestamp when this sitemap analysis job finished.';
COMMENT ON COLUMN "sitemap_analyses"."failure_stage" IS 'Pipeline stage where this sitemap analysis failed, if any.';
COMMENT ON COLUMN "sitemap_analyses"."fetch_duration_seconds" IS 'Total time spent fetching and parsing sitemap data.';
COMMENT ON COLUMN "sitemap_analyses"."root_sitemap_url" IS 'Root sitemap URL inspected by the sitemap analysis job.';
COMMENT ON COLUMN "sitemap_analyses"."total_sitemaps" IS 'Number of sitemap files discovered during analysis.';
COMMENT ON COLUMN "sitemap_analyses"."total_urls" IS 'Number of URLs discovered across all sitemap files.';
COMMENT ON COLUMN "sitemap_analyses"."issue_summary" IS 'Structured summary of sitemap issues by category.';
COMMENT ON COLUMN "sitemap_analyses"."issues" IS 'Structured list of sitemap issues found by deterministic checks.';
COMMENT ON COLUMN "sitemap_analyses"."summary" IS 'LLM-generated sitemap analysis summary.';
COMMENT ON COLUMN "sitemap_analyses"."severity" IS 'LLM-classified severity for this sitemap analysis.';
COMMENT ON COLUMN "sitemap_analyses"."key_findings" IS 'List of important findings extracted from the sitemap analysis.';
COMMENT ON COLUMN "sitemap_analyses"."recommendations" IS 'LLM-generated sitemap recommendations.';
COMMENT ON COLUMN "sitemap_analyses"."trend_summary" IS 'LLM-generated trend comparison against prior sitemap analyses.';
COMMENT ON COLUMN "sitemap_analyses"."execution_time_seconds" IS 'Total wall-clock execution time for this sitemap analysis job.';
COMMENT ON COLUMN "sitemap_analyses"."gpt_tokens_used" IS 'OpenAI token count used for this sitemap analysis.';
COMMENT ON COLUMN "sitemap_analyses"."gpt_cost_usd" IS 'Estimated OpenAI API cost in USD for this sitemap analysis.';
COMMENT ON COLUMN "sitemap_analyses"."email_sent" IS 'Whether the sitemap analysis email was sent.';
COMMENT ON COLUMN "sitemap_analyses"."error_message" IS 'Error message captured when this sitemap analysis failed.';
COMMENT ON TABLE "sitemap_analyses" IS 'Stored sitemap-analysis report and execution state.';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "ViNoP1OcomtaaXL/8DCsJQ6Q=="
)
