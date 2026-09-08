import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app import scheduler_main
from app.core.config import settings


@pytest.mark.asyncio
async def test_run_partition_maintenance_does_not_raise():
    """Confirms `CALL public.run_maintenance_proc()` actually executes
    against the real pg_partman install (verified signature/schema location
    during Task 3 — public, not partman) on an AUTOCOMMIT connection, rather
    than assuming the SQL/isolation handling is correct."""
    await scheduler_main.run_partition_maintenance()


@pytest.mark.asyncio
async def test_purge_expired_evidence_deletes_old_rows_and_respects_retention(admin_session, tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler_main, "EVIDENCE_ROOT", tmp_path)

    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    camera_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam')"),
        {"id": camera_id, "tid": tenant_id},
    )

    old_id, recent_id = uuid.uuid4(), uuid.uuid4()
    old_rel_path = f"{tenant_id}/old.jpg"
    recent_rel_path = f"{tenant_id}/recent.jpg"
    (tmp_path / str(tenant_id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / old_rel_path).write_bytes(b"old")
    (tmp_path / recent_rel_path).write_bytes(b"recent")

    now = datetime.now(timezone.utc)
    await admin_session.execute(
        text(
            "INSERT INTO evidence (id, tenant_id, media_type, storage_path, checksum_sha256, captured_at) "
            "VALUES (:id, :tid, 'image', :path, 'x', :captured_at)"
        ),
        {"id": old_id, "tid": tenant_id, "path": old_rel_path, "captured_at": now - timedelta(days=200)},
    )
    await admin_session.execute(
        text(
            "INSERT INTO evidence (id, tenant_id, media_type, storage_path, checksum_sha256, captured_at) "
            "VALUES (:id, :tid, 'image', :path, 'x', :captured_at)"
        ),
        {"id": recent_id, "tid": tenant_id, "path": recent_rel_path, "captured_at": now - timedelta(days=1)},
    )
    await admin_session.commit()

    deleted_count = await scheduler_main.purge_expired_evidence(admin_session)

    assert deleted_count >= 1
    remaining = (
        await admin_session.execute(text("SELECT id FROM evidence WHERE tenant_id = :tid"), {"tid": tenant_id})
    ).fetchall()
    remaining_ids = {r.id for r in remaining}
    assert old_id not in remaining_ids  # past the 90-day default retention -> purged
    assert recent_id in remaining_ids  # within retention -> kept
    assert not (tmp_path / old_rel_path).exists()  # file deleted too, not just the row
    assert (tmp_path / recent_rel_path).exists()

    # Cleanup
    await admin_session.execute(text("DELETE FROM evidence WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM cameras WHERE tenant_id = :tid"), {"tid": tenant_id})
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()


@pytest.mark.asyncio
async def test_archive_old_audit_partitions_detaches_and_exports(admin_session, tmp_path, monkeypatch):
    """Manufactures an 'already expired' scenario (AUDIT_RETENTION_YEARS=0,
    so the cutoff is ~now) against one specific, otherwise-empty partition in
    the TEST database — never touches the dev database's real audit_logs.
    Confirms detach (never DROP, per plan §16.1's compliance requirement) +
    JSONL export actually happen, not just that the SQL is well-formed.

    pg_partman only pre-creates current+future partitions (p_premake=3), so
    a past month's partition must be created explicitly here before we can
    insert into it. We use 2 months ago so it is guaranteed to be before
    the AUDIT_RETENTION_YEARS=0 cutoff (which is ~now)."""
    monkeypatch.setattr(settings, "AUDIT_RETENTION_YEARS", 0)
    monkeypatch.setattr(scheduler_main, "ARCHIVE_ROOT", tmp_path)

    # Compute a partition 2 months in the past that is guaranteed to not
    # already exist (pg_partman only pre-creates future months).
    now = datetime.now(timezone.utc)
    # Step back 2 months safely (handles Jan/Feb wrap-around).
    m = now.month - 2
    y = now.year + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    part_start = datetime(y, m, 1, tzinfo=timezone.utc)
    part_end = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=timezone.utc)
    partition_name = f"audit_logs_p{y}{m:02d}01"
    test_date = datetime(y, m, 15, tzinfo=timezone.utc)

    tenant_id = uuid.uuid4()
    await admin_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'T', :slug)"),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )
    await admin_session.commit()

    # Create the past partition explicitly — the INSERT would silently route
    # to audit_logs_default otherwise, and the default partition is never archived.
    existing = {
        r[0] for r in (
            await admin_session.execute(text("SELECT partition_tablename FROM public.show_partitions('public.audit_logs')"))
        ).fetchall()
    }
    partition_existed = partition_name in existing
    if not partition_existed:
        # Distinguish "no such table" from "table exists but is detached".
        # This test DETACHes the partition and re-ATTACHes it at the end; if a
        # run dies in between (a pytest-timeout kill, say), the table survives
        # — that non-destructive detach is the compliance behaviour under test
        # — but drops out of show_partitions() while still owning the name.
        # Checking partition membership alone therefore reports "safe to
        # create", CREATE TABLE fails with DuplicateTable, and the test is
        # wedged permanently on every future run. Re-attach the orphan.
        orphan = (
            await admin_session.execute(
                text("SELECT to_regclass(:n)"), {"n": f"public.{partition_name}"}
            )
        ).scalar()
        if orphan is not None:
            # Drop rather than re-attach. An orphan can predate a migration
            # that added columns to audit_logs, and ATTACH then fails with
            # "child table is missing column" — which is how this was actually
            # found. Dropping sidesteps schema drift entirely, and is safe
            # here in a way it would never be in production: a detached
            # partition in the TEST database exists only because an earlier
            # test run died mid-way, so it holds nothing but that run's
            # fixture rows.
            await admin_session.execute(text(f"DROP TABLE {partition_name}"))
        await admin_session.execute(text(
            f"CREATE TABLE {partition_name} PARTITION OF public.audit_logs "
            f"FOR VALUES FROM ('{part_start.isoformat()}') TO ('{part_end.isoformat()}')"
        ))
        await admin_session.commit()

    audit_id = uuid.uuid4()
    await admin_session.execute(
        text(
            "INSERT INTO audit_logs (id, tenant_id, action, resource_type, resource_id, created_at) "
            "VALUES (:id, :tid, 'test_action', 'detection', :rid, :created_at)"
        ),
        {"id": audit_id, "tid": tenant_id, "rid": uuid.uuid4(), "created_at": test_date},
    )
    await admin_session.commit()

    archived = await scheduler_main.archive_old_audit_partitions(admin_session)

    assert partition_name in archived

    export_file = tmp_path / f"{partition_name}.jsonl"
    assert export_file.exists()
    exported_rows = [json.loads(line) for line in export_file.read_text().splitlines()]
    assert any(row["id"] == str(audit_id) for row in exported_rows)

    # Detached, not dropped: the table still exists standalone.
    remaining_partitions = (
        await admin_session.execute(text("SELECT partition_tablename FROM public.show_partitions('public.audit_logs')"))
    ).fetchall()
    assert partition_name not in [r[0] for r in remaining_partitions]

    still_exists = (
        await admin_session.execute(text(f"SELECT count(*) FROM {partition_name}"))
    ).first()
    assert still_exists[0] == 1  # row is physically there, just detached

    # Cleanup: re-attach so a second run finds audit_logs intact.
    await admin_session.execute(text(f"DELETE FROM {partition_name} WHERE id = :id"), {"id": audit_id})
    await admin_session.execute(
        text(
            f"ALTER TABLE public.audit_logs ATTACH PARTITION {partition_name} "
            f"FOR VALUES FROM ('{part_start.isoformat()}') TO ('{part_end.isoformat()}')"
        )
    )
    if not partition_existed:
        # We created it for this test; detach and drop so the DB is clean.
        await admin_session.execute(text(f"ALTER TABLE public.audit_logs DETACH PARTITION {partition_name}"))
        await admin_session.execute(text(f"DROP TABLE {partition_name}"))
    await admin_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
    await admin_session.commit()


# ─── The audit archiver's connection requirements ────────────────────────────
#
# These pin WHY archive_old_audit_partitions needs a privileged session. The
# test above has always passed it an admin_session, so it passed while
# production — which handed it the ordinary app session — aborted the whole
# nightly cycle on the first partition read. Both facts below were verified
# against the running stack before being written down.

@pytest.mark.asyncio
async def test_app_session_cannot_detach_a_partition(admin_session, db_session):
    """DETACH PARTITION needs ownership of audit_logs, which svc_app lacks.

    This alone means the archive job could never have completed on the app
    connection, whatever else was true about row-level security.
    """
    partition = (
        await admin_session.execute(
            text("SELECT partition_tablename FROM public.show_partitions('public.audit_logs') LIMIT 1")
        )
    ).scalar()
    assert partition, "no audit partitions exist to test against"

    with pytest.raises(Exception) as exc:
        await db_session.execute(
            text(f"ALTER TABLE public.audit_logs DETACH PARTITION {partition}")
        )
    assert "must be owner" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_a_committed_set_local_poisons_the_pooled_connection(db_session):
    """The second reason, and the one that actually produced the nightly error.

    A transaction-scoped set_config does not vanish at commit — it reverts to
    the session value, which for a GUC never set at session level is the EMPTY
    STRING rather than NULL. Every RLS policy then casts ''::uuid and raises.
    A connection where the GUC was never set is fine, because current_setting
    returns NULL and NULL::uuid is legal, which is exactly why this was
    invisible in isolation and only appeared after purge_expired_evidence had
    run on the same pooled connection.
    """
    before = (
        await db_session.execute(text("SELECT current_setting('app.current_tenant', true)"))
    ).scalar()
    assert before is None, "this test needs a connection whose tenant GUC was never set"

    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(uuid.uuid4())},
    )
    await db_session.commit()

    after = (
        await db_session.execute(text("SELECT current_setting('app.current_tenant', true)"))
    ).scalar()
    assert after == "", (
        "SET LOCAL should leave the GUC as an empty string after commit; if this "
        "changed, the archive job may no longer need its own connection"
    )
