"""incidents, incident_events, health counters, monitor thresholds

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)
EMPTY_OBJ = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "monitors",
        sa.Column("failure_threshold", sa.SmallInteger, server_default="3", nullable=False),
    )
    op.add_column(
        "monitors",
        sa.Column("recovery_threshold", sa.SmallInteger, server_default="2", nullable=False),
    )
    # Short names: the metadata naming convention adds the ck_monitors_ prefix.
    op.create_check_constraint(
        "failure_threshold", "monitors", "failure_threshold BETWEEN 1 AND 10"
    )
    op.create_check_constraint(
        "recovery_threshold", "monitors", "recovery_threshold BETWEEN 1 AND 10"
    )

    op.add_column(
        "monitor_state",
        sa.Column("consecutive_failures", sa.Integer, server_default="0", nullable=False),
    )
    op.add_column(
        "monitor_state",
        sa.Column("consecutive_successes", sa.Integer, server_default="0", nullable=False),
    )
    op.add_column(
        "monitor_state",
        sa.Column("incident_open", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )
    op.add_column("monitor_state", sa.Column("streak_first_run_id", sa.BigInteger))
    op.add_column("monitor_state", sa.Column("streak_first_at", TZ))
    op.add_column(
        "monitor_state",
        sa.Column("streak_class_counts", pg.JSONB, server_default=EMPTY_OBJ, nullable=False),
    )
    op.add_column(
        "monitor_state",
        sa.Column("incident_class_counts", pg.JSONB, server_default=EMPTY_OBJ, nullable=False),
    )

    op.create_table(
        "incidents",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("monitor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("opened_at", TZ, nullable=False),
        sa.Column("resolved_at", TZ),
        sa.Column("first_failure_run_id", sa.BigInteger, nullable=False),
        sa.Column("resolving_run_id", sa.BigInteger),
        sa.Column("failure_count", sa.Integer, nullable=False),
        sa.Column("dominant_failure_class", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_incidents"),
        sa.ForeignKeyConstraint(
            ["monitor_id"], ["monitors.id"], ondelete="CASCADE", name="fk_incidents_monitor_id"
        ),
        sa.ForeignKeyConstraint(
            ["first_failure_run_id"],
            ["check_runs.id"],
            ondelete="RESTRICT",
            name="fk_incidents_first_failure_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["resolving_run_id"],
            ["check_runs.id"],
            ondelete="RESTRICT",
            name="fk_incidents_resolving_run_id",
        ),
        sa.CheckConstraint("status IN ('OPEN','RESOLVED')", name="status"),
    )
    # The database itself guarantees at most one open incident per monitor.
    op.create_index(
        "one_open_incident",
        "incidents",
        ["monitor_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index("ix_incidents_monitor_opened", "incidents", ["monitor_id", "opened_at"])

    op.create_table(
        "incident_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("incident_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("at", TZ, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("run_id", sa.BigInteger),
        sa.Column("detail", pg.JSONB, server_default=EMPTY_OBJ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_incident_events"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            ondelete="CASCADE",
            name="fk_incident_events_incident_id",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["check_runs.id"], ondelete="RESTRICT", name="fk_incident_events_run_id"
        ),
        sa.CheckConstraint(
            "kind IN ('OPENED','FAILURE_CLASS_CHANGED','RECOVERING','RESOLVED')", name="kind"
        ),
    )
    op.create_index("ix_incident_events_incident_at", "incident_events", ["incident_id", "at"])


def downgrade() -> None:
    op.drop_table("incident_events")
    op.drop_table("incidents")
    for c in (
        "incident_class_counts",
        "streak_class_counts",
        "streak_first_at",
        "streak_first_run_id",
        "incident_open",
        "consecutive_successes",
        "consecutive_failures",
    ):
        op.drop_column("monitor_state", c)
    op.drop_constraint("recovery_threshold", "monitors", type_="check")
    op.drop_constraint("failure_threshold", "monitors", type_="check")
    op.drop_column("monitors", "recovery_threshold")
    op.drop_column("monitors", "failure_threshold")
