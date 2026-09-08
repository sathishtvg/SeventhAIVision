"""Shared handover logic: the register counts, and the checklist snapshot.

WHY THIS EXISTS
    Two callers now need the same two answers — "what does the site owe at this
    moment?" and "what should this shift's checklist be?":

        routers/shifts.py    creates the handover at end of shift
        routers/handover.py  reads, accepts and disputes it afterwards

    Keeping the gathering here means the numbers written onto a handover and
    the numbers the acceptance screen reasons about cannot drift apart, which
    is the same call already made for services/attendance_status.py.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# What a checklist item can be measured against. The value must match the
# expected_source CHECK in migration 0099.
COUNT_SOURCES = ("keys_out", "lost_found_held", "equipment_out")


async def gather_site_position(db: AsyncSession, site_id) -> dict:
    """What the site owes right now: keys out, property held, kit signed out,
    defects still live.

    All five queries are site-scoped when the shift has a site and tenant-wide
    when it does not — a roving shift with no site still hands over, and
    reporting nothing for it would be worse than reporting the tenant total.
    """
    params = {"site_id": str(site_id)} if site_id else {}
    key_filter = "AND k.site_id = CAST(:site_id AS uuid)" if site_id else ""
    lf_filter = "AND i.site_id = CAST(:site_id AS uuid)" if site_id else ""
    eq_filter = "AND e.site_id = CAST(:site_id AS uuid)" if site_id else ""
    df_filter = "AND d.site_id = CAST(:site_id AS uuid)" if site_id else ""

    keys = (await db.execute(
        text(f"""
            SELECT COUNT(*)::int AS out_count,
                   COUNT(*) FILTER (
                       WHERE t.expected_return_at IS NOT NULL
                         AND t.expected_return_at < now()
                   )::int AS overdue_count
              FROM key_transactions t
              JOIN site_keys k ON k.id = t.key_id
             WHERE t.returned_at IS NULL {key_filter}
        """),
        params,
    )).first()

    lost_found = (await db.execute(
        text(f"""
            SELECT COUNT(*)::int FROM lost_found_items i
             WHERE i.status = 'held' {lf_filter}
        """),
        params,
    )).scalar()

    equipment = (await db.execute(
        text(f"""
            SELECT COUNT(*)::int
              FROM equipment_assignments a
              JOIN equipment_items e ON e.id = a.item_id
             WHERE a.returned_at IS NULL {eq_filter}
        """),
        params,
    )).scalar()

    defects = (await db.execute(
        text(f"""
            SELECT COUNT(*)::int FROM facility_defects d
             WHERE d.status IN ('open', 'reported', 'in_progress') {df_filter}
        """),
        params,
    )).scalar()

    return {
        "keys_outstanding": keys.out_count if keys else 0,
        "keys_overdue": keys.overdue_count if keys else 0,
        "lost_found_held": lost_found or 0,
        "equipment_out_count": equipment or 0,
        "open_defects_count": defects or 0,
    }


async def resolve_template_id(db: AsyncSession, site_id) -> str | None:
    """The site's own active checklist, else the tenant-wide fallback.

    One company-standard list plus per-site exceptions is how these are
    actually maintained, so a site with no template of its own is not a site
    with no checklist.
    """
    if site_id is not None:
        row = (await db.execute(
            text("SELECT id FROM handover_checklist_templates "
                 "WHERE site_id = CAST(:sid AS uuid) AND is_active"),
            {"sid": str(site_id)},
        )).first()
        if row is not None:
            return str(row.id)

    row = (await db.execute(
        text("SELECT id FROM handover_checklist_templates "
             "WHERE site_id IS NULL AND is_active"),
    )).first()
    return str(row.id) if row else None


async def snapshot_checklist(
    db: AsyncSession, handover_id: str, site_id, position: dict,
) -> int:
    """Copy the applicable template's items onto this handover.

    Copied, not referenced: a template edited next month must not rewrite what
    somebody signed last month. Each item that measures against a register also
    carries what the system believed at this moment, so a mismatch at
    acceptance is visible without recomputing history.

    Returns how many items were written. Zero is a legitimate answer — a tenant
    that has set up no checklist still gets a handover, just an unstructured
    one, which is exactly what they had before.
    """
    template_id = await resolve_template_id(db, site_id)
    if template_id is None:
        return 0

    items = (await db.execute(
        text("""
            SELECT id, label, requires_count, expected_source, is_required, sort_order
              FROM handover_checklist_items
             WHERE template_id = CAST(:tid AS uuid)
          ORDER BY sort_order, label
        """),
        {"tid": template_id},
    )).mappings().all()
    if not items:
        return 0

    source_to_value = {
        "keys_out": position["keys_outstanding"],
        "lost_found_held": position["lost_found_held"],
        "equipment_out": position["equipment_out_count"],
    }

    for item in items:
        await db.execute(
            text("""
                INSERT INTO handover_checks
                    (tenant_id, handover_id, item_id, label, requires_count,
                     is_required, sort_order, expected_value)
                VALUES (current_setting('app.current_tenant')::uuid,
                        CAST(:hid AS uuid), CAST(:iid AS uuid), :label, :needs_count,
                        :required, :sort, :expected)
            """),
            {
                "hid": handover_id,
                "iid": str(item["id"]),
                "label": item["label"],
                "needs_count": item["requires_count"],
                "required": item["is_required"],
                "sort": item["sort_order"],
                "expected": source_to_value.get(item["expected_source"] or ""),
            },
        )
    return len(items)
