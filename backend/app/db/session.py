"""Schema design note: Phase 1's highest-volume tables (detections and its
per-module children, evidence, audit_logs) are Postgres-partitioned with composite
primary keys (id, detected_at) — see plan §2/§16. SQLAlchemy's declarative ORM
doesn't map partitioned tables or their composite-key FK relationships cleanly,
and fighting it would buy nothing here. So: the Alembic migration
(alembic/versions/0001_initial_schema.py) is the single hand-written source of
truth for DDL + RLS policies + pg_partman registration, and routers/services/
workers query through SQLAlchemy Core (text()-based parameterized SQL) rather
than ORM model classes. Pydantic schemas in app/schemas/ are the typed I/O layer.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
