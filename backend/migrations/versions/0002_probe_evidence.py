"""probe evidence: phase timings, attempts, assertions, response sample

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSON_EMPTY_OBJ = sa.text("'{}'::jsonb")
JSON_EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column(
        "monitors", sa.Column("headers", pg.JSONB, server_default=JSON_EMPTY_OBJ, nullable=False)
    )
    op.add_column(
        "monitors", sa.Column("assertions", pg.JSONB, server_default=JSON_EMPTY_ARR, nullable=False)
    )
    op.add_column("monitors", sa.Column("degraded_ms", sa.Integer))
    for name in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms"):
        op.add_column("check_runs", sa.Column(name, sa.Integer))
    op.add_column("check_runs", sa.Column("resolved_ip", pg.INET))
    op.add_column(
        "check_runs",
        sa.Column("attempt_count", sa.SmallInteger, server_default="1", nullable=False),
    )
    op.add_column(
        "check_runs", sa.Column("attempts", pg.JSONB, server_default=JSON_EMPTY_ARR, nullable=False)
    )
    op.add_column(
        "check_runs",
        sa.Column("assertion_results", pg.JSONB, server_default=JSON_EMPTY_ARR, nullable=False),
    )
    op.add_column("check_runs", sa.Column("response_headers", pg.JSONB))
    op.add_column("check_runs", sa.Column("response_snippet", sa.Text))
    op.add_column(
        "check_runs",
        sa.Column("body_truncated", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "body_truncated", "response_snippet", "response_headers", "assertion_results",
        "attempts", "attempt_count", "resolved_ip", "ttfb_ms", "tls_ms", "connect_ms", "dns_ms",
    ):  # fmt: skip
        op.drop_column("check_runs", name)
    for name in ("degraded_ms", "assertions", "headers"):
        op.drop_column("monitors", name)
