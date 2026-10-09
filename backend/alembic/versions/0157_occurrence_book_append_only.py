"""The occurrence book is added to, and the database now says so too.

Found in phase 5 and left for the owner (ENTERPRISE_SECURITY_HARDENING.md,
section 9, item 1); decided on 2026-10-09.

The book has always been described as append-only: an entry is never edited or
removed, and a mistake is corrected by a further entry. No code does either -
the three places that write the book (the guard's entry, a correction, an SOS)
only add a row. But the application's database role held UPDATE and DELETE on
`occurrence_book_entries`, so what kept the book whole was that nobody had
written the statement that would break it.

This takes the two rights away. The role keeps SELECT and INSERT, which is all
the application uses.

WHAT STILL REMOVES A ROW, and is not the application's role: an organisation
being removed, and the cascade from a shift or a site being removed - each
carried out by the table's owner, as the database's own rule.

Revision ID: 0157
Revises: 0156
"""
from alembic import op

revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE, DELETE ON occurrence_book_entries FROM svc_app")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON occurrence_book_entries TO svc_app")
