"""Drop legacy FLOAT4[] embedding columns from face tables.

Migration 0005 added embedding_v vector(512) with a backfill from the original
FLOAT4[] embedding column and created an HNSW cosine index. Both columns have
been maintained in parallel since then. Now that every write path (watchlist
router and face worker) populates embedding_v, the legacy column is dead weight:
~2 KB per row (512 × float4), duplicated across face_watchlist_entries and every
face_events partition.

This migration also adds a partial HNSW index on face_events.embedding_v for
future cross-detection similarity queries (currently nullable — only rows written
after migration 0005 have it set).

Revision ID: 0021
Revises: 0020
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── face_watchlist_entries ────────────────────────────────────────────────
    # embedding_v is NOT NULL + HNSW-indexed (created in 0005). Safe to drop
    # the original FLOAT4[] column — it is no longer written or read by any code.
    op.execute(
        "ALTER TABLE face_watchlist_entries DROP COLUMN IF EXISTS embedding"
    )

    # ── face_events (partitioned) ─────────────────────────────────────────────
    # Drop the legacy FLOAT4[] column from the parent; Postgres cascades to all
    # existing partitions automatically.
    op.execute(
        "ALTER TABLE face_events DROP COLUMN IF EXISTS embedding"
    )

    # Add a partial HNSW index on face_events.embedding_v for future similarity
    # search against historical detections. PARTIAL (WHERE embedding_v IS NOT NULL)
    # so the index skips rows from before migration 0005 that were never backfilled.
    # Named explicitly so downgrade() can drop it precisely.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS face_events_embedding_v_hnsw
        ON face_events USING hnsw (embedding_v vector_cosine_ops)
        WHERE embedding_v IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS face_events_embedding_v_hnsw")
    # Restore the FLOAT4[] columns as nullable (historical values are gone).
    op.execute(
        "ALTER TABLE face_events ADD COLUMN IF NOT EXISTS embedding float4[]"
    )
    op.execute(
        "ALTER TABLE face_watchlist_entries ADD COLUMN IF NOT EXISTS embedding float4[]"
    )
