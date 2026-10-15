"""initial: users, monitors, monitor_state, check_runs

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "monitors",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("method", sa.Text, server_default="GET", nullable=False),
        sa.Column("interval_seconds", sa.Integer, server_default="60", nullable=False),
        sa.Column("timeout_ms", sa.Integer, server_default="5000", nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_monitors"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="CASCADE", name="fk_monitors_owner_id_users"
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_monitors_owner_id"),
        sa.CheckConstraint("method IN ('GET','HEAD')", name="ck_monitors_method"),
        sa.CheckConstraint("interval_seconds BETWEEN 30 AND 3600", name="ck_monitors_interval"),
        sa.CheckConstraint("timeout_ms BETWEEN 1000 AND 30000", name="ck_monitors_timeout"),
    )
    op.create_index("ix_monitors_owner_created", "monitors", ["owner_id", "created_at"])
    op.create_table(
        "monitor_state",
        sa.Column("monitor_id", pg.UUID(as_uuid=True)),
        sa.Column("next_run_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("health", sa.Text, server_default="UNKNOWN", nullable=False),
        sa.Column("last_run_id", sa.BigInteger),
        sa.Column("last_run_at", TZ),
        sa.PrimaryKeyConstraint("monitor_id", name="pk_monitor_state"),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            ondelete="CASCADE",
            name="fk_monitor_state_monitor_id_monitors",
        ),
        sa.CheckConstraint(
            "health IN ('UNKNOWN','UP','DEGRADED','DOWN','PAUSED')", name="ck_monitor_state_health"
        ),
    )
    op.create_table(
        "check_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("monitor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", TZ, nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("finished_at", TZ, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("failure_class", sa.Text),
        sa.Column("http_status", sa.SmallInteger),
        sa.Column("total_ms", sa.Integer),
        sa.Column("error_detail", sa.Text),
        sa.Column("worker_id", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, server_default="schedule", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_check_runs"),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            ondelete="CASCADE",
            name="fk_check_runs_monitor_id_monitors",
        ),
        sa.UniqueConstraint("monitor_id", "scheduled_for", name="uq_check_runs_monitor_id"),
        sa.CheckConstraint(
            "outcome IN ('PASS','DEGRADED','FAIL','INCONCLUSIVE')", name="ck_check_runs_outcome"
        ),
        sa.CheckConstraint(
            "(failure_class IS NULL) = (outcome IN ('PASS','DEGRADED'))",
            name="ck_check_runs_failure_class",
        ),
    )
    op.create_index(
        "ix_check_runs_monitor_started", "check_runs", ["monitor_id", sa.text("started_at DESC")]
    )
    op.create_index("ix_check_runs_started", "check_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("check_runs")
    op.drop_table("monitor_state")
    op.drop_table("monitors")
    op.drop_table("users")
