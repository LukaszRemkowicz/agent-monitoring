from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "log_analysis_llm_calls" ADD "model_name" VARCHAR(160);
        ALTER TABLE "log_analysis_llm_calls" ADD "usage_raw" JSONB;
        ALTER TABLE "log_analysis_llm_calls" ADD "total_tokens" INT;
        ALTER TABLE "log_analysis_llm_calls" ADD "cost_usd" DOUBLE PRECISION;
        ALTER TABLE "log_analysis_llm_calls" ADD "request_character_count" INT;
        ALTER TABLE "log_analysis_llm_calls" ADD "provider_name" VARCHAR(80);
        ALTER TABLE "log_analysis_llm_calls" ADD "completion_tokens" INT;
        ALTER TABLE "log_analysis_llm_calls" ADD "prompt_tokens" INT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "model_name";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "usage_raw";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "total_tokens";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "cost_usd";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "request_character_count";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "provider_name";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "completion_tokens";
        ALTER TABLE "log_analysis_llm_calls" DROP COLUMN "prompt_tokens";"""


MODELS_STATE = (
    "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"
    "CNUusO1P0HjJRk9zmY/p+u6HRh+fp/nT2zKw=="
)
