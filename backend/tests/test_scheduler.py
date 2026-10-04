import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app import scheduler_main
from app.core.config import settings


# ─── Partition maintenance ───────────────────────────────────────────────────
#
# These pass the superuser session, as run_once does. The function used to take
# none and run on the app connection, where it could never add a partition; the
# only test of it called it and checked that nothing was raised - which was
# true, because the failure was caught and logged.

async def _partitions(admin, parent: str) -> dict[str, dict]:
    rows = (await admin.execute(text("""
        SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity AS protected,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
          FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = CAST(:p AS regclass)
    """), {"p": parent})).mappings().all()
    await admin.commit()
    return {r["relname"]: dict(r) for r in rows}


async def _where(admin, table: str, column: str, value) -> str | None:
    """Which partition a row is in."""
    found = await admin.scalar(text(f"SELECT tableoid::regclass::text FROM {table} WHERE {column} = :v"), {"v": value})
    await admin.commit()
    return found


@pytest.mark.asyncio
async def test_partition_maintenance_makes_the_months_ahead_and_protects_them(admin_session):
    """The job's whole purpose, which it had never once achieved: a month that is
    missing gets made, as the scheduler runs it, and arrives with row security."""
    parent = "public.weapon_events"
    before = await _partitions(admin_session, parent)
    ahead = sorted(n for n in before if not n.endswith("_default"))[-2:]
    for name in ahead:
        assert await admin_session.scalar(text(f"SELECT count(*) FROM {name}")) == 0, f"{name} is not empty"
        await admin_session.execute(text(f"DROP TABLE {name}"))
    await admin_session.commit()
    assert not set(ahead) & set(await _partitions(admin_session, parent)), "the months ahead were not removed"

    result = await scheduler_main.run_partition_maintenance(admin_session)

    assert result["failed"] == {}
    after = await _partitions(admin_session, parent)
    assert set(ahead) <= set(after), f"not made again: {sorted(set(ahead) - set(after))}"
    assert set(ahead) <= set(result["created"])
    unprotected = [n for n, row in after.items() if not (row["protected"] and row["policies"] == 1)]
    assert unprotected == [], f"partitions without forced row security: {unprotected}"

    # And every partitioned table, not only this one, has the month three ahead.
    unready = (await admin_session.execute(text("""
        SELECT pc.parent_table FROM public.part_config pc
         WHERE NOT EXISTS (
            SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
             WHERE i.inhparent = pc.parent_table::regclass
               AND c.relname = split_part(pc.parent_table, '.', 2) || '_p'
                               || to_char(date_trunc('month', now()) + interval '3 months', 'YYYYMMDD'))
    """))).scalars().all()
    await admin_session.commit()
    assert unready == []
    assert await scheduler_main.secure_new_partitions() == 0, "maintenance left a partition for the sweep to secure"


@pytest.mark.asyncio
async def test_every_partitioned_table_keeps_getting_partitions_while_it_is_quiet(admin_session):
    """pg_partman's default makes new partitions only while rows keep arriving. A
    table that has been quiet for a season - weapon events, on a good year - would
    have no partition for today. Checked for every set, so one registered by a
    later migration without the setting fails here."""
    stops = (await admin_session.execute(text(
        "SELECT parent_table FROM public.part_config WHERE NOT infinite_time_partitions"))).scalars().all()
    await admin_session.commit()
    assert stops == []


@pytest.mark.asyncio
async def test_rows_stranded_in_a_default_partition_are_moved_with_everything_linked_to_them(admin_session):
    """What an installation that ran out of partitions is left with: rows in the
    default. The night the job first works it must move them to where they belong
    - and not, as pg_partman's own move does, delete everything that referenced
    them. A detection moved that way loses its plate row (ON DELETE CASCADE fires
    on the delete half of the move); this is the test that it does not."""
    when = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)      # before any month any installation has
    tenant, camera, detection, audit = (uuid.uuid4() for _ in range(4))
    made = ("lpr_events_p20250101", "audit_logs_p20250101", "detections_p20250101")
    for stmt, params in (
        ("INSERT INTO tenants (id, name, slug) VALUES (:t, 'Stranded', :s)", {"t": tenant, "s": f"str-{tenant.hex[:10]}"}),
        ("INSERT INTO cameras (id, tenant_id, name) VALUES (:c, :t, 'Gate')", {"c": camera, "t": tenant}),
        ("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, bounding_box, raw_metadata, "
         "detected_at) VALUES (:d, :t, :c, 'lpr', 0.9, '{}', '{}', :at)",
         {"d": detection, "t": tenant, "c": camera, "at": when}),
        ("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number) "
         "VALUES (:d, :at, :t, :c, 'SBA1234A')", {"d": detection, "t": tenant, "c": camera, "at": when}),
        ("INSERT INTO audit_logs (id, tenant_id, action, created_at, row_hash, prev_hash) "
         "VALUES (:a, :t, 'test.stranded', :at, 'h1', 'h0')", {"a": audit, "t": tenant, "at": when}),
    ):
        await admin_session.execute(text(stmt), params)
    await admin_session.commit()
    try:
        assert await _where(admin_session, "detections", "id", detection) == "detections_default"
        assert await _where(admin_session, "lpr_events", "detection_id", detection) == "lpr_events_default"
        assert await _where(admin_session, "audit_logs", "id", audit) == "audit_logs_default"
        audit_before = await admin_session.scalar(text("SELECT md5(a::text) FROM audit_logs a WHERE id = :a"), {"a": audit})
        await admin_session.commit()

        result = await scheduler_main.run_partition_maintenance(admin_session)

        assert result["failed"] == {}
        for parent in ("public.detections", "public.lpr_events", "public.audit_logs"):
            assert result["moved"].get(parent, 0) >= 1, result["moved"]
        assert await _where(admin_session, "detections", "id", detection) == "detections_p20250101"
        assert await _where(admin_session, "lpr_events", "detection_id", detection) == "lpr_events_p20250101", \
            "the plate row did not survive its detection being moved"
        assert await _where(admin_session, "audit_logs", "id", audit) == "audit_logs_p20250101"
        audit_after = await admin_session.scalar(text("SELECT md5(a::text) FROM audit_logs a WHERE id = :a"), {"a": audit})
        assert audit_after == audit_before, "the audit row changed while it was moved"
        left = await admin_session.scalar(text(
            "SELECT (SELECT count(*) FROM ONLY detections_default) + (SELECT count(*) FROM ONLY lpr_events_default) "
            "     + (SELECT count(*) FROM ONLY audit_logs_default)"))
        assert left == 0

        # The months it made for them are protected like any other.
        for parent, name in (("public.detections", made[2]), ("public.lpr_events", made[0]), ("public.audit_logs", made[1])):
            row = (await _partitions(admin_session, parent))[name]
            assert row["protected"] and row["policies"] == 1, name
        # The triggers it suspended are back: the link still deletes with its parent.
        assert await admin_session.scalar(text("SELECT current_setting('session_replication_role')")) == "origin"
        await admin_session.execute(text("DELETE FROM detections WHERE id = :d"), {"d": detection})
        await admin_session.commit()
        assert await _where(admin_session, "lpr_events", "detection_id", detection) is None
    finally:
        await admin_session.rollback()
        for stmt in ("DELETE FROM audit_logs WHERE tenant_id = :t", "DELETE FROM detections WHERE tenant_id = :t",
                     "DELETE FROM cameras WHERE tenant_id = :t", "DELETE FROM tenants WHERE id = :t"):
            await admin_session.execute(text(stmt), {"t": tenant})
        await admin_session.commit()
        # Leave the schema as it was. A partition of a referenced table has to be
        # detached before it can be dropped.
        for name in made:
            parent = name.rsplit("_p", 1)[0]
            exists = await admin_session.scalar(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"})
            if exists:
                await admin_session.execute(text(f"ALTER TABLE public.{parent} DETACH PARTITION public.{name}"))
                await admin_session.execute(text(f"DROP TABLE public.{name}"))
        await admin_session.commit()


@pytest.mark.asyncio
async def test_one_tables_trouble_is_that_tables_alone(admin_session, monkeypatch, caplog):
    """Each table is maintained in its own transaction. One that cannot be - and
    there will be one, some night - must not cost the others their partitions, and
    must be reported as an error rather than a warning nobody reads."""
    real = scheduler_main._move_stranded_rows

    async def breaks_for_one(db, parent):
        if parent == "public.crowd_events":
            raise RuntimeError("no room on the disk")
        return await real(db, parent)

    monkeypatch.setattr(scheduler_main, "_move_stranded_rows", breaks_for_one)
    with caplog.at_level("ERROR"):
        result = await scheduler_main.run_partition_maintenance(admin_session)
    assert list(result["failed"]) == ["public.crowd_events"]
    assert "no room on the disk" in result["failed"]["public.crowd_events"]
    assert any("partition maintenance FAILED for public.crowd_events" in r.getMessage() for r in caplog.records)
    # The session is usable afterwards, and the other thirteen were done.
    sets = await admin_session.scalar(text("SELECT count(*) FROM public.part_config WHERE automatic_maintenance = 'on'"))
    await admin_session.commit()
    assert sets >= 14


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
