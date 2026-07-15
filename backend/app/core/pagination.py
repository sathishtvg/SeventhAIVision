"""Shared pagination helper — avoids duplicating the COUNT + data query pattern."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_LIMIT = 200


def clamp(limit: int, offset: int) -> tuple[int, int]:
    return min(max(limit, 1), MAX_LIMIT), max(offset, 0)


async def paginate(
    db: AsyncSession,
    data_sql: str,
    count_sql: str,
    params: dict,
    limit: int,
    offset: int,
) -> dict:
    """Run data_sql (with :limit/:offset injected) and count_sql in sequence.

    Returns:
        { items, total, limit, offset, has_more }
    """
    cap, safe_offset = clamp(limit, offset)
    data_result  = await db.execute(text(data_sql),  {**params, "limit": cap, "offset": safe_offset})
    count_result = await db.execute(text(count_sql), params)
    items = [dict(row._mapping) for row in data_result]
    total = count_result.scalar_one()
    return {
        "items":    items,
        "total":    total,
        "limit":    cap,
        "offset":   safe_offset,
        "has_more": safe_offset + cap < total,
    }
