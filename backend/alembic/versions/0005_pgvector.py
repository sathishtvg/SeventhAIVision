"""Phase 9: pgvector extension + embedding_v vector(512) columns on
face_watchlist_entries and face_events.

face_watchlist_entries: backfill from existing FLOAT4[] embedding column then
create an HNSW cosine index — enables single SQL ANN query instead of
fetching the whole watchlist to Python for brute-force cosine comparison.

face_events: add embedding_v for future cross-detection similarity queries;
no index yet (search against historical detections is not a current feature).

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector extension — idempotent; the pgvector/pgvector:pg16 Docker image
    # ships the shared library, but CREATE EXTENSION still needs to run once per DB.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── face_watchlist_entries ──────────────────────────────────────────────
    # Add the vector column (nullable initially so the UPDATE can populate it).
    op.execute(
        "ALTER TABLE face_watchlist_entries ADD COLUMN IF NOT EXISTS embedding_v vector(512)"
    )
    # Backfill from existing FLOAT4[]: postgres array text looks like {0.1,0.2,...};
    # pgvector vector literal looks like [0.1,0.2,...] — swap braces for brackets.
    op.execute(
        """
        UPDATE face_watchlist_entries
        SET embedding_v = ('[' || btrim(embedding::text, '{}') || ']')::vector
        WHERE embedding_v IS NULL
        """
    )
    # Now safe to enforce NOT NULL (every existing row is backfilled, new rows
    # must supply the value — enforced by the watchlist router and face worker).
    op.execute(
        "ALTER TABLE face_watchlist_entries ALTER COLUMN embedding_v SET NOT NULL"
    )
    # HNSW index (pgvector 0.5+) for sub-millisecond ANN cosine search.
    # Works with any data size including empty tables (unlike IVFFlat which needs
    # training data). Named explicitly so downgrade can drop it by name.
    op.execute(
        """
        CREATE INDEX face_watchlist_embedding_v_hnsw
        ON face_watchlist_entries USING hnsw (embedding_v vector_cosine_ops)
        """
    )

    # ── face_events (partitioned table) ────────────────────────────────────
    # Nullable: historical rows won't be backfilled (embedding was never stored
    # as vector), new rows written by the face worker will populate it.
    op.execute(
        "ALTER TABLE face_events ADD COLUMN IF NOT EXISTS embedding_v vector(512)"
    )
    # No ANN index here yet — historical detection similarity search is not a
    # current feature. Add in a future migration when the use case is concrete.


def downgrade() -> None:
    op.execute("ALTER TABLE face_events DROP COLUMN IF EXISTS embedding_v")
    op.execute("DROP INDEX IF EXISTS face_watchlist_embedding_v_hnsw")
    op.execute(
        "ALTER TABLE face_watchlist_entries DROP COLUMN IF EXISTS embedding_v"
    )
    # Do not drop the extension — other objects may depend on it after this migration ran.
