from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "email_deliveries" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "report_kind" VARCHAR(40) NOT NULL,
    "report_id" INT,
    "analysis_date" DATE,
    "recipient_target" VARCHAR(40) NOT NULL,
    "recipients" JSONB NOT NULL,
    "subject" TEXT NOT NULL,
    "status" VARCHAR(20) NOT NULL,
    "attempted_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "sent_at" TIMESTAMPTZ,
    "provider_message_id" VARCHAR(255),
    "error_message" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_email_deliv_created_21b9b6" ON "email_deliveries" ("created_at");
CREATE INDEX IF NOT EXISTS "idx_email_deliv_report__36db06" ON "email_deliveries" ("report_kind");
CREATE INDEX IF NOT EXISTS "idx_email_deliv_report__e59207" ON "email_deliveries" ("report_id");
CREATE INDEX IF NOT EXISTS "idx_email_deliv_analysi_057573" ON "email_deliveries" ("analysis_date");
CREATE INDEX IF NOT EXISTS "idx_email_deliv_recipie_821bdb" ON "email_deliveries" ("recipient_target");
CREATE INDEX IF NOT EXISTS "idx_email_deliv_status_d64088" ON "email_deliveries" ("status");
CREATE INDEX IF NOT EXISTS "idx_email_deliv_attempt_bef89a" ON "email_deliveries" ("attempted_at");
COMMENT ON COLUMN "email_deliveries"."created_at" IS 'UTC timestamp when this email delivery attempt was recorded.';
COMMENT ON COLUMN "email_deliveries"."report_kind" IS 'Report or notification kind this email attempted to deliver.';
COMMENT ON COLUMN "email_deliveries"."report_id" IS 'Stored report id when this delivery belongs to a report row.';
COMMENT ON COLUMN "email_deliveries"."analysis_date" IS 'Analysis date associated with this delivery attempt.';
COMMENT ON COLUMN "email_deliveries"."recipient_target" IS 'Recipient group used for this delivery attempt.';
COMMENT ON COLUMN "email_deliveries"."recipients" IS 'Email recipients used for this attempt.';
COMMENT ON COLUMN "email_deliveries"."subject" IS 'Rendered email subject used for this attempt.';
COMMENT ON COLUMN "email_deliveries"."status" IS 'Delivery attempt status.';
COMMENT ON COLUMN "email_deliveries"."attempted_at" IS 'UTC timestamp when this delivery was attempted.';
COMMENT ON COLUMN "email_deliveries"."sent_at" IS 'UTC timestamp when the delivery succeeded.';
COMMENT ON COLUMN "email_deliveries"."provider_message_id" IS 'Provider message id when available.';
COMMENT ON COLUMN "email_deliveries"."error_message" IS 'Error captured when the delivery attempt failed.';
COMMENT ON TABLE "email_deliveries" IS 'One monitoring email delivery attempt.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "email_deliveries";"""


MODELS_STATE = (
    "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"
    "lXkZit3TpqLWzS6fby9f/uU3h5"
)
