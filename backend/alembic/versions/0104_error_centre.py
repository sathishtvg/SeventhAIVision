"""An error centre, not an error log.

0103 gave failures somewhere to land: one row per occurrence. That is a log,
and a log is the wrong shape for the question the platform owner actually asks.
One bad deploy writes forty thousand identical rows, and "is anything broken"
becomes a scrolling exercise. What they need to know is how MANY distinct
things are broken, which customers each one touches, when it started, and
whether anyone has dealt with it.

So the same failure seen four thousand times is ONE row here, with a count.

  platform_errors        the distinct problem: what broke, how badly, since
                         when, how often, and whether it is resolved
  platform_error_events  each individual occurrence: which tenant, which
                         request, which stack

GROUPING IS BY FINGERPRINT, a hash of the service, the exception type and the
route with its variable parts removed. /users/6f3a.../documents and
/users/91bc.../documents are the same bug seen twice, and a fingerprint that
included the id would say they were two. The collector computes it; the unique
index makes the upsert honest under concurrency.

SEVERITY IS ASSIGNED, not guessed later. §18 wants three bands, and they mean
different things to whoever is on call: critical is the platform being down,
warning is it degrading, info is a thing that happened.

RESOLUTION LIVES ON THE GROUP, because you resolve a problem, not an instance
of a problem.

WHY EVENTS ARE KEPT AT ALL, when the group carries the count: the group tells
you something is wrong, the events tell you who it is happening to. "Which
customers is this hurting" is unanswerable from a counter.

No RLS on either. They span tenants by definition and are read through
platform:read, which Super Admin alone holds.

Revision ID: 0104
Revises: 0103
"""
from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0103's table was one row per occurrence and nothing has written to it
    # yet, so it is replaced outright rather than migrated.
    op.execute("DROP TABLE IF EXISTS platform_errors")

    op.execute("""
        CREATE TABLE platform_errors (
            id               UUID PRIMARY KEY,
            fingerprint      TEXT        NOT NULL,
            service          VARCHAR(50) NOT NULL DEFAULT 'api',
            severity         VARCHAR(20) NOT NULL DEFAULT 'critical',
            error_code       VARCHAR(100),
            error_type       VARCHAR(200),
            message          TEXT,
            method           VARCHAR(10),
            path_pattern     TEXT,
            first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            occurrence_count INTEGER     NOT NULL DEFAULT 1,
            -- open -> acknowledged -> resolved. Reopens by going back to open
            -- when a resolved fingerprint is seen again, because a fix that
            -- did not hold is worse news than a new bug.
            status           VARCHAR(20) NOT NULL DEFAULT 'open',
            resolution       TEXT,
            resolved_at      TIMESTAMPTZ,
            resolved_by      UUID,
            CONSTRAINT ck_platform_error_severity
                CHECK (severity IN ('critical', 'warning', 'info')),
            CONSTRAINT ck_platform_error_status
                CHECK (status IN ('open', 'acknowledged', 'resolved'))
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_errors IS
            'One row per distinct problem, not per occurrence. Grouped by a '
            'fingerprint over service, exception type and the route with its '
            'variable parts removed.'
    """)
    # The upsert depends on this being unique, not merely indexed.
    op.execute("""
        CREATE UNIQUE INDEX uq_platform_error_fingerprint
            ON platform_errors (fingerprint)
    """)
    # The console's default view: what is still open, worst first, newest first.
    op.execute("""
        CREATE INDEX idx_platform_errors_open
            ON platform_errors (status, severity, last_seen_at DESC)
    """)

    op.execute("""
        CREATE TABLE platform_error_events (
            id          UUID PRIMARY KEY,
            error_id    UUID        NOT NULL
                        REFERENCES platform_errors(id) ON DELETE CASCADE,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id   UUID REFERENCES tenants(id) ON DELETE SET NULL,
            user_id     UUID,
            status_code INTEGER,
            request_id  VARCHAR(64),
            path        TEXT,
            stack       TEXT
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_error_events IS
            'Each occurrence of a platform_errors row. Kept because the group '
            'says something is wrong and only these say who it is happening to.'
    """)
    op.execute("""
        CREATE INDEX idx_platform_error_events_group
            ON platform_error_events (error_id, occurred_at DESC)
    """)
    # "Which of my customers is affected" — the question the group cannot answer.
    op.execute("""
        CREATE INDEX idx_platform_error_events_tenant
            ON platform_error_events (tenant_id, occurred_at DESC)
            WHERE tenant_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_error_events")
    op.execute("DROP TABLE IF EXISTS platform_errors")
    # 0103's shape, so downgrading to it leaves a working table.
    op.execute("""
        CREATE TABLE platform_errors (
            id           UUID PRIMARY KEY,
            occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id    UUID REFERENCES tenants(id) ON DELETE SET NULL,
            user_id      UUID,
            method       VARCHAR(10),
            path         TEXT,
            status_code  INTEGER,
            error_type   VARCHAR(200),
            message      TEXT,
            stack        TEXT
        )
    """)
