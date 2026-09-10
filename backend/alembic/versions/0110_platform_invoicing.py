"""Invoices the vendor writes, as opposed to invoices Stripe reports.

There are already two invoice tables and neither can do this.

  billing_invoices    a mirror of Stripe. stripe_invoice_id is NOT NULL, so
                      nothing can be created here that Stripe did not issue.
                      Useful for reconciliation, useless for billing a customer
                      who pays by bank transfer, which in this market is most
                      of them.

  invoices            the TENANT billing THEIR clients — guard hours at a site
                      rate. A completely different commercial relationship that
                      happens to share the word.

So these are new, and prefixed platform_ like platform_errors, because the
prefix is what stops the third reader confusing the vendor's ledger with the
customer's.

WHAT AN INVOICE IS HERE. A statement with line items, issued to one tenant for
one period. §14 wants it created, edited, cancelled, credited, paid and
refunded, and each of those is a state or a payment row rather than an edit
that loses what came before.

    draft      being prepared. The only state that may be edited.
    issued     sent. Line items are frozen.
    paid       settled in full.
    overdue    issued, past due, not settled.
    cancelled  withdrawn before payment.
    credited   reversed after issue, by a credit note.

AMOUNTS ARE NON-NEGATIVE EXCEPT ON A CREDIT NOTE, where they must be negative.
Requiring positives everywhere is the obvious constraint and the wrong one: it
blocks the document that reverses a mistake.

A DRAFT IS THE ONLY EDITABLE STATE, enforced by a trigger rather than by the
API remembering to check. An invoice somebody has already been sent must not
change underneath them; correcting it means a credit note and a new one, which
is what an auditor expects to find and what a customer can follow.

PAYMENTS ARE ROWS, NOT A COLUMN. A part payment, a second part payment and a
refund are three facts, and amount_paid is their sum. Storing only the total
throws away the history that answers "when did they actually pay".

MONEY IS NUMERIC. Never float. A cent of drift is a customer who stops
trusting the entire bill.

NO RLS. These belong to the vendor, span tenants, and are reached only through
billing:read and billing:manage, which Super Admin alone holds since 0103.

Revision ID: 0110
Revises: 0109
"""
from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_invoices (
            id                 UUID PRIMARY KEY,
            tenant_id          UUID        NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            invoice_number     VARCHAR(40) NOT NULL,
            status             VARCHAR(20) NOT NULL DEFAULT 'draft',
            currency           VARCHAR(10) NOT NULL DEFAULT 'SGD',
            period_start       DATE,
            period_end         DATE,
            issued_at          TIMESTAMPTZ,
            due_date           DATE,
            paid_at            TIMESTAMPTZ,
            subtotal           NUMERIC(12,2) NOT NULL DEFAULT 0,
            discount_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
            tax_rate           NUMERIC(5,4)  NOT NULL DEFAULT 0,
            tax_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
            total_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
            notes              TEXT,
            -- Set when this invoice reverses another. A credit note is an
            -- invoice pointing at the one it cancels, which keeps both in the
            -- ledger where an auditor can see the correction happen.
            credits_invoice_id UUID REFERENCES platform_invoices(id) ON DELETE SET NULL,
            created_by_user_id UUID,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_platform_invoice_status CHECK (
                status IN ('draft', 'issued', 'paid', 'overdue', 'cancelled', 'credited')
            ),
            -- Negative amounts are legal on a credit note and nowhere else.
            -- A blanket "must be positive" is the obvious rule and the wrong
            -- one: it blocks the very document that reverses a mistake, which
            -- is the thing an auditor most wants to be able to find.
            CONSTRAINT ck_platform_invoice_amounts CHECK (
                (credits_invoice_id IS NULL
                     AND subtotal >= 0 AND tax_amount >= 0)
                OR (credits_invoice_id IS NOT NULL
                     AND subtotal <= 0 AND tax_amount <= 0)
            ),
            CONSTRAINT ck_platform_invoice_discount CHECK (discount_amount >= 0)
        )
    """)
    # An invoice number is a reference a customer quotes back. Two invoices
    # sharing one is a support conversation nobody can resolve.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_invoice_number
            ON platform_invoices (invoice_number)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_invoices_tenant
            ON platform_invoices (tenant_id, issued_at DESC)
    """)
    # "What is outstanding" is the query this table exists to answer.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_invoices_unpaid
            ON platform_invoices (status, due_date)
            WHERE status IN ('issued', 'overdue')
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_invoice_items (
            id          UUID PRIMARY KEY,
            invoice_id  UUID          NOT NULL
                        REFERENCES platform_invoices(id) ON DELETE CASCADE,
            -- What produced this line, so a bill can be explained rather than
            -- merely totalled: "base", "module", "camera", "site", "user",
            -- "storage", "adjustment".
            kind        VARCHAR(20)   NOT NULL DEFAULT 'adjustment',
            module_code VARCHAR(50) REFERENCES billing_modules(code) ON DELETE SET NULL,
            description TEXT          NOT NULL,
            quantity    NUMERIC(12,2) NOT NULL DEFAULT 1,
            unit_price  NUMERIC(10,2) NOT NULL DEFAULT 0,
            line_total  NUMERIC(12,2) NOT NULL DEFAULT 0,
            sort_order  INTEGER       NOT NULL DEFAULT 0,
            CONSTRAINT ck_invoice_item_kind CHECK (
                kind IN ('base', 'module', 'camera', 'site', 'user',
                         'storage', 'adjustment', 'discount')
            )
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_invoice_items
            ON platform_invoice_items (invoice_id, sort_order)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_payments (
            id                 UUID PRIMARY KEY,
            invoice_id         UUID NOT NULL
                               REFERENCES platform_invoices(id) ON DELETE CASCADE,
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            -- Negative for a refund. One column rather than a flag, so the sum
            -- of the rows is what is owed without anybody remembering to
            -- subtract the right ones.
            amount             NUMERIC(12,2) NOT NULL,
            method             VARCHAR(30) NOT NULL DEFAULT 'bank_transfer',
            reference          VARCHAR(120),
            received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            recorded_by_user_id UUID,
            notes              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_platform_payment_method CHECK (
                method IN ('bank_transfer', 'card', 'cheque', 'cash',
                           'stripe', 'credit_note', 'other')
            ),
            CONSTRAINT ck_platform_payment_nonzero CHECK (amount <> 0)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_payments_invoice
            ON platform_payments (invoice_id, received_at DESC)
    """)

    # ── A draft is the only editable state ───────────────────────────────────
    #
    # In the trigger rather than the API, because "the endpoint checks" is a
    # promise every future endpoint has to keep, and one of them will not.
    # Status changes are always allowed — that is how an invoice progresses —
    # but the numbers and the period may not move once it has been sent.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_issued_invoice_is_frozen()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'draft' THEN
                RETURN NEW;
            END IF;
            IF NEW.subtotal        IS DISTINCT FROM OLD.subtotal
            OR NEW.discount_amount IS DISTINCT FROM OLD.discount_amount
            OR NEW.tax_rate        IS DISTINCT FROM OLD.tax_rate
            OR NEW.tax_amount      IS DISTINCT FROM OLD.tax_amount
            OR NEW.total_amount    IS DISTINCT FROM OLD.total_amount
            OR NEW.period_start    IS DISTINCT FROM OLD.period_start
            OR NEW.period_end      IS DISTINCT FROM OLD.period_end
            OR NEW.tenant_id       IS DISTINCT FROM OLD.tenant_id
            THEN
                RAISE EXCEPTION
                    'invoice % has been issued and cannot be edited', OLD.invoice_number
                    USING HINT = 'Raise a credit note and issue a corrected invoice, '
                                 'so the correction is visible instead of the '
                                 'original quietly changing.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_issued_invoice_frozen
            BEFORE UPDATE ON platform_invoices
            FOR EACH ROW EXECUTE FUNCTION enforce_issued_invoice_is_frozen()
    """)

    # Line items on an issued invoice are equally frozen. Same reasoning: the
    # total is checked above, and this stops the parts moving under it.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_issued_invoice_items_frozen()
        RETURNS trigger AS $$
        DECLARE
            inv_status VARCHAR(20);
            inv_id     UUID;
        BEGIN
            inv_id := COALESCE(NEW.invoice_id, OLD.invoice_id);
            SELECT status INTO inv_status FROM platform_invoices WHERE id = inv_id;
            IF inv_status IS NOT NULL AND inv_status <> 'draft' THEN
                RAISE EXCEPTION 'invoice items cannot change after the invoice is issued'
                    USING HINT = 'Raise a credit note and issue a corrected invoice.';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_issued_invoice_items_frozen
            BEFORE INSERT OR UPDATE OR DELETE ON platform_invoice_items
            FOR EACH ROW EXECUTE FUNCTION enforce_issued_invoice_items_frozen()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_issued_invoice_items_frozen "
               "ON platform_invoice_items")
    op.execute("DROP FUNCTION IF EXISTS enforce_issued_invoice_items_frozen()")
    op.execute("DROP TRIGGER IF EXISTS trg_issued_invoice_frozen ON platform_invoices")
    op.execute("DROP FUNCTION IF EXISTS enforce_issued_invoice_is_frozen()")
    op.execute("DROP TABLE IF EXISTS platform_payments")
    op.execute("DROP TABLE IF EXISTS platform_invoice_items")
    op.execute("DROP TABLE IF EXISTS platform_invoices")
