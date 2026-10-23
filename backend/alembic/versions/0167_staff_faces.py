"""Staff face registration and the check-in face match.

Design: docs/plans/2026-10-23-staff-face-registration-design.md, agreed with the
owner on 23 October 2026. A registered face confirms that the person checking in
is the guard on the shift; it is not the camera watchlist (face_watchlist_entries),
which is not touched.

* staff_faces: one face per person and organisation - the face code (vector(512),
  as the watchlist keeps it), the registration photo, how it was registered and
  the consent record.
* shift_face_checks: the outcome of comparing each check-in and check-out selfie
  with the face on file. The owner chose "allow but flag": a check never blocks a
  check-in, it is recorded for a supervisor to review.
* face:register_self (a person registers their own face once) and face:manage
  (a supervisor or admin changes one; who may change whose is applied in
  app/services/staff_faces.py).

Revision ID: 0167
Revises: 0166
"""
from alembic import op

revision = "0167"
down_revision = "0166"
branch_labels = None
depends_on = None

PERMISSIONS = (
    ("face:register_self", "Register your own face for check-in, once", "attendance"),
    ("face:manage", "Register, replace or delete the face of a staff member for check-in", "attendance"),
)
#: Everyone who checks in may register themselves; supervisors, managers and admins may change a face. The
#: platform owner (1) holds neither: they work no customer's shifts and see no customer's staff (migration 0102).
ROLES = tuple((r, "face:register_self") for r in (2, 3, 4, 5, 8)) + tuple((r, "face:manage") for r in (2, 3, 8))

SOURCES = ("SELFIE", "UPLOAD")
OUTCOMES = ("MATCHED", "MISMATCH", "NO_FACE", "NOT_CHECKED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _secure(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute(f"REVOKE ALL ON {table} FROM svc_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO svc_app")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE staff_faces (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            embedding_v           vector(512) NOT NULL,
            photo_path            TEXT NOT NULL,
            source                VARCHAR(10) NOT NULL,
            liveness_score        NUMERIC(5,4),
            consent_id            UUID REFERENCES pdpa_consents(id) ON DELETE SET NULL,
            registered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_staff_faces_user UNIQUE (tenant_id, user_id),
            CONSTRAINT ck_staff_faces_source CHECK (source IN ({_in(SOURCES)}))
        )
    """)
    _secure("staff_faces")

    op.execute(f"""
        CREATE TABLE shift_face_checks (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            shift_id        UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            guard_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            which           VARCHAR(10) NOT NULL,
            outcome         VARCHAR(12) NOT NULL,
            similarity      NUMERIC(5,4),
            min_similarity  NUMERIC(5,4),
            reason          TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_shift_face_checks UNIQUE (shift_id, which),
            CONSTRAINT ck_shift_face_checks_which CHECK (which IN ('check_in', 'check_out')),
            CONSTRAINT ck_shift_face_checks_outcome CHECK (outcome IN ({_in(OUTCOMES)}))
        )
    """)
    op.execute("CREATE INDEX idx_shift_face_checks_review ON shift_face_checks (tenant_id, outcome, created_at DESC)")
    _secure("shift_face_checks")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    values = ", ".join(f"({role}, '{code}')" for role, code in ROLES)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES {values}) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c, _, _ in PERMISSIONS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
    op.execute("DROP TABLE shift_face_checks")
    op.execute("DROP TABLE staff_faces")
