"""job_runs: records housekeeping executions (retention)

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("job_name", sa.Text, nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("finished_at", TZ),
        sa.Column("rows_affected", sa.Integer, server_default="0", nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("worker_id", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_job_runs"),
    )
    op.create_index("ix_job_runs_name_started", "job_runs", ["job_name", "started_at"])


def downgrade() -> None:
    op.drop_table("job_runs")
