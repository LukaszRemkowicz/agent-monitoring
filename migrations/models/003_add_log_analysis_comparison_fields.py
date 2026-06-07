from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "log_analyses" ADD "known_patterns" JSONB NOT NULL DEFAULT '[]'::jsonb;
        ALTER TABLE "log_analyses" ADD "deterministic_fingerprint" JSONB NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE "log_analyses" ADD "coverage_snapshot" JSONB NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE "log_analyses" ADD "fingerprint_version" VARCHAR(40) NOT NULL DEFAULT '';
        ALTER TABLE "log_analyses" ADD "evidence_fingerprints" JSONB NOT NULL DEFAULT '[]'::jsonb;
        COMMENT ON COLUMN "log_analyses"."known_patterns" IS 'Known recurring log patterns available to future runs.';
COMMENT ON COLUMN "log_analyses"."deterministic_fingerprint" IS 'Compact deterministic facts derived from MCP artifacts and tool results.';
COMMENT ON COLUMN "log_analyses"."coverage_snapshot" IS 'Source coverage snapshot used to compare current and baseline runs.';
COMMENT ON COLUMN "log_analyses"."fingerprint_version" IS 'Version of the structured history fingerprint format.';
COMMENT ON COLUMN "log_analyses"."evidence_fingerprints" IS 'Stable evidence fingerprints used for baseline comparison.';
        CREATE INDEX IF NOT EXISTS "idx_log_analyse_fingerp_b24812" ON "log_analyses" ("fingerprint_version");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_log_analyse_fingerp_b24812";
        ALTER TABLE "log_analyses" DROP COLUMN "known_patterns";
        ALTER TABLE "log_analyses" DROP COLUMN "deterministic_fingerprint";
        ALTER TABLE "log_analyses" DROP COLUMN "coverage_snapshot";
        ALTER TABLE "log_analyses" DROP COLUMN "fingerprint_version";
        ALTER TABLE "log_analyses" DROP COLUMN "evidence_fingerprints";"""


MODELS_STATE = (
    "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"
)
