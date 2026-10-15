"""worker: leases, pause flag, heartbeats, claim indexes

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.add_column(
        "monitors",
        sa.Column("is_paused", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )
    # Data model says 30..3600; the 30 s floor is enforced by the API (MIN_INTERVAL_S) so
    # tests can use short intervals. See build-plan deviations.
    op.drop_constraint("interval", "monitors", type_="check")
    # Short name: the metadata naming convention prefixes it to ck_monitors_interval.
    op.create_check_constraint("interval", "monitors", "interval_seconds BETWEEN 1 AND 3600")

    op.add_column("monitor_state", sa.Column("lease_owner", sa.Text))
    op.add_column("monitor_state", sa.Column("lease_expires_at", TZ))
    # Partial index: only unleased rows, ordered by due time (the common claim path).
    op.create_index(
        "ix_state_due",
        "monitor_state",
        ["next_run_at"],
        postgresql_where=sa.text("lease_owner IS NULL"),
    )
    # Expired-lease reclaim path.
    op.create_index(
        "ix_state_lease_expires",
        "monitor_state",
        ["lease_expires_at"],
        postgresql_where=sa.text("lease_owner IS NOT NULL"),
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.Text),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("last_seen_at", TZ, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("in_flight", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_error", sa.Text),
        sa.PrimaryKeyConstraint("worker_id", name="pk_worker_heartbeats"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_state_lease_expires", "monitor_state")
    op.drop_index("ix_state_due", "monitor_state")
    op.drop_column("monitor_state", "lease_expires_at")
    op.drop_column("monitor_state", "lease_owner")
    # Lossy by necessity: rows created under the relaxed rule may be below the old floor.
    op.execute("UPDATE monitors SET interval_seconds = 30 WHERE interval_seconds < 30")
    op.drop_constraint("interval", "monitors", type_="check")
    op.create_check_constraint("interval", "monitors", "interval_seconds BETWEEN 30 AND 3600")
    op.drop_column("monitors", "is_paused")
