"""simplification: drop worker_heartbeats and job_runs

The System page, heartbeats and the retention job were removed to keep the project small
enough to explain. The tables are recreated on downgrade so history stays reversible.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("worker_heartbeats")


def downgrade() -> None:
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
