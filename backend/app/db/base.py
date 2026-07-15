from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Not used for table definitions in Phase 1 — see backend/app/db/session.py
    docstring for why schema lives in a hand-written Alembic migration instead of
    declarative ORM models. Kept as the metadata anchor Alembic's env.py expects."""
