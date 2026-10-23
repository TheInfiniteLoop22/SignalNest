"""index check_runs.started_at so the hourly retention delete does not scan the whole table

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The existing (monitor_id, started_at) index cannot serve "older than N days" across all
    # monitors, so without this the cleanup reads every row (15 ms at 200,000 rows, growing).
    op.execute("CREATE INDEX ix_check_runs_started_at ON check_runs (started_at)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_check_runs_started_at")
