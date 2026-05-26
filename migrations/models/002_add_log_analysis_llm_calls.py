from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "log_analysis_llm_calls" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "trace_id" VARCHAR(64) NOT NULL,
    "analysis_date" DATE,
    "workflow_name" VARCHAR(160),
    "mcp_session_id" VARCHAR(255),
    "iteration" INT,
    "step_type" VARCHAR(80) NOT NULL,
    "action" VARCHAR(80),
    "tool_name" VARCHAR(160),
    "skill_name" VARCHAR(160),
    "requested_tool_names_text" TEXT NOT NULL,
    "requested_skill_names_text" TEXT NOT NULL,
    "arguments_hash" VARCHAR(64),
    "arguments_text" TEXT NOT NULL,
    "status" VARCHAR(40),
    "duplicate_skipped" BOOL NOT NULL DEFAULT False,
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "duration_ms" INT,
    "llm_response_text" TEXT NOT NULL,
    "error_message" TEXT NOT NULL,
    "result_summary" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS "idx_log_analysi_trace_i_f286d8" ON "log_analysis_llm_calls" ("trace_id");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_analysi_69b537" ON "log_analysis_llm_calls" ("analysis_date");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_workflo_9b88dd" ON "log_analysis_llm_calls" ("workflow_name");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_iterati_ab6b25" ON "log_analysis_llm_calls" ("iteration");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_step_ty_0841ac" ON "log_analysis_llm_calls" ("step_type");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_action_ce9d64" ON "log_analysis_llm_calls" ("action");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_tool_na_4a5ec4" ON "log_analysis_llm_calls" ("tool_name");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_skill_n_5341d7" ON "log_analysis_llm_calls" ("skill_name");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_argumen_ef34f7" ON "log_analysis_llm_calls" ("arguments_hash");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_status_8d02ef" ON "log_analysis_llm_calls" ("status");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_duplica_f0317c" ON "log_analysis_llm_calls" ("duplicate_skipped");
CREATE INDEX IF NOT EXISTS "idx_log_analysi_created_c3dd33" ON "log_analysis_llm_calls" ("created_at");
COMMENT ON COLUMN "log_analysis_llm_calls"."trace_id" IS 'Run-local trace id grouping LLM calls from one command execution.';
COMMENT ON COLUMN "log_analysis_llm_calls"."analysis_date" IS 'Analysis date associated with this LLM call.';
COMMENT ON COLUMN "log_analysis_llm_calls"."step_type" IS 'Kind of agent-loop step, such as llm_action_received or mcp_tool_call.';
COMMENT ON TABLE "log_analysis_llm_calls" IS 'One persisted LLM/tool-loop decision from a log-analysis run.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "log_analysis_llm_calls";"""


MODELS_STATE = (
    "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"
    "MRskbWVi9nbraGdpk1bdy7f/AyaHXLw="
)
