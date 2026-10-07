"""The SOP library: which version of a procedure is in force, its passages, and finding the passage on something.

  passages()   cuts a procedure's text into the passages it is made of, each
               under the heading it stands under. Pure.
  IN_FORCE     of each procedure's approved versions whose date has come, the
               latest version — and only if it has not run out
  ask()        finds the passages of procedures in force that use the words of
               a question, and returns them as they were approved
  relevant()   the procedures in force for one kind of incident at one site

NOTHING HERE WRITES AN ANSWER (owner decision E2). `ask` returns passages of
approved procedures, word for word, each with the procedure, the version and
who approved it. It ranks by how many of the question's words a passage uses.
When no passage uses any of them it says so; it does not fall back to
something near, and it does not compose.

ONLY WHAT IS IN FORCE IS FOUND. A draft, a version awaiting approval, a
rejected version, a version that has been replaced or has run out, and a
retired procedure are never returned by `ask` or `relevant`.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CATEGORIES = ("general", "emergency", "fire", "medical", "evacuation", "access", "visitor", "patrol",
              "incident_response", "equipment", "contacts")
#: The kinds of incident a procedure may be for that are always offered. The
#: first part of an alert code: `intrusion.zone_breach` is an `intrusion`.
KNOWN_TYPES = ("intrusion", "fire_smoke", "weapon", "crowd", "behavior", "abandoned", "tampering", "fall", "ppe",
               "face", "lpr", "access", "alarm", "guard", "camera")
TYPE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_PASSAGES = 8
#: The most passages one version is cut into. A procedure longer than this is several procedures.
MAX_PASSAGES_PER_VERSION = 400
ASK_NOTE = ("These are passages of approved procedures in force, exactly as they were approved. Nothing here was "
            "written in answer to the question.")
NOTHING = "No approved procedure in force uses those words. Nothing nearer was looked for."

#: Of each procedure's approved versions whose date has come, the latest
#: version: a later version replaces an earlier one from its date, whichever
#: date is the earlier. The caller still has to leave out the one that has
#: run out, and the retired.
IN_FORCE = """
    SELECT DISTINCT ON (v.document_id) v.*
      FROM sop_versions v
     WHERE v.state = 'APPROVED' AND v.effective_from <= :now
     ORDER BY v.document_id, v.version_no DESC
"""
STANDING = "NOT d.is_retired AND (f.effective_until IS NULL OR f.effective_until > :now)"

_HASHES = re.compile(r"^#{1,6}\s+")


def _in_capitals(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(line) <= 80 and len(letters) >= 3 and all(c.isupper() for c in letters)


def _heading(block: list[str]) -> str | None:
    """A paragraph that is a heading, or None. A heading is one line: marked
    with #, written in capitals, or ending in a colon."""
    if len(block) != 1:
        return None
    line = block[0].strip()
    if _HASHES.match(line):
        return _HASHES.sub("", line).strip().rstrip(":").strip() or None
    if _in_capitals(line):
        return line.rstrip(":").strip()
    if len(line) <= 80 and line.endswith(":"):
        return line[:-1].strip() or None
    return None


def passages(body: str) -> list[dict]:
    """A procedure's text as the passages it is made of, in order: each
    paragraph, with the heading it stands under. The words are the
    procedure's own; only the blank lines between paragraphs are dropped."""
    out: list[dict] = []
    heading: str | None = None
    block: list[str] = []

    def close() -> None:
        nonlocal heading, block
        if not block:
            return
        # A line in capitals is a heading even with its text straight under it.
        if len(block) > 1 and _in_capitals(block[0].strip()):
            heading, block = block[0].strip().rstrip(":").strip(), block[1:]
        title = _heading(block)
        if title is not None:
            heading = title
        else:
            out.append({"heading": heading, "body": "\n".join(line.rstrip() for line in block).strip()})
        block = []

    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            # A heading marked with # ends the paragraph before it even with no blank line between.
            if _HASHES.match(line.strip()) and block:
                close()
            block.append(line)
            if _HASHES.match(line.strip()):
                close()
        else:
            close()
    close()
    return out[:MAX_PASSAGES_PER_VERSION]


def site_clause(allowed: list[str] | None, site_id: Any, params: dict) -> str:
    """A procedure for every site is everybody's; one for a site belongs to
    whoever may see that site. With a site named: that site's, and the ones for
    every site."""
    if site_id is not None:
        params["site"] = str(site_id)
        return "(d.site_id IS NULL OR d.site_id = CAST(:site AS uuid))"
    if allowed is None:
        return "TRUE"
    params["allowed_sites"] = list(allowed)
    return "(d.site_id IS NULL OR d.site_id::text = ANY(:allowed_sites))"


_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_LEXEME = re.compile(r"^[\w.+-]+$", re.UNICODE)


async def words_of(db: AsyncSession, question: str) -> dict[str, str]:
    """The words of a question that a procedure can be found by, as the search
    reads them: `{what the search looks for: the word as it was asked}`. Words
    like "the" and "what" are not among them."""
    asked = list(dict.fromkeys(_WORD.findall(question)))[:60]
    if not asked:
        return {}
    rows = await db.execute(text(
        "SELECT w AS word, tsvector_to_array(to_tsvector('english', w)) AS read FROM unnest(CAST(:w AS text[])) w"),
        {"w": asked})
    found: dict[str, str] = {}
    for r in rows:
        for lexeme in r.read or ():
            if _LEXEME.match(lexeme):
                found.setdefault(lexeme, r.word)
    return found


async def ask(db: AsyncSession, question: str, now: datetime, allowed: list[str] | None, *,
              site_id: Any = None, limit: int = MAX_PASSAGES) -> dict:
    """The passages of procedures in force that use the words of the question,
    most of its words first. Each as it was approved, with where it is from."""
    words = await words_of(db, question)
    answer: dict[str, Any] = {"question": question, "words": sorted(set(words.values()), key=str.lower),
                              "passages": [], "note": ASK_NOTE, "is_an_answer": False}
    if not words:
        answer["nothing"] = "Ask with the words you would look for: a thing, a place, something that has happened."
        return answer
    params: dict = {"now": now, "lexemes": list(words), "limit": limit,
                    "query": " | ".join("'" + w.replace("'", "''") + "'" for w in words)}
    rows = (await db.execute(text(f"""
        SELECT p.id, p.ordinal, p.heading, p.body, d.id AS document_id, d.code, d.title, d.category, d.site_id,
               s.name AS site_name, f.id AS version_id, f.version_no, f.decided_at AS approved_at,
               u.full_name AS approved_by_name, f.effective_from, f.effective_until,
               ARRAY(SELECT w FROM unnest(tsvector_to_array(p.tsv)) w WHERE w = ANY(:lexemes)) AS hits,
               ts_rank_cd(p.tsv, CAST(:query AS tsquery)) AS rank
          FROM sop_passages p
          JOIN ({IN_FORCE}) f ON f.id = p.version_id
          JOIN sop_documents d ON d.id = f.document_id
          LEFT JOIN sites s ON s.id = d.site_id
          LEFT JOIN users u ON u.id = f.decided_by_user_id
         WHERE p.tsv @@ CAST(:query AS tsquery) AND {STANDING} AND {site_clause(allowed, site_id, params)}
         ORDER BY cardinality(ARRAY(SELECT w FROM unnest(tsvector_to_array(p.tsv)) w WHERE w = ANY(:lexemes))) DESC,
                  rank DESC, d.code, p.ordinal
         LIMIT :limit
    """), params)).mappings().all()
    for r in rows:
        answer["passages"].append({
            "id": r["id"], "heading": r["heading"], "text": r["body"],
            "matched_words": sorted({words[h] for h in r["hits"] if h in words}, key=str.lower),
            "matched": len(set(r["hits"])), "of": len(words),
            "procedure": {"id": r["document_id"], "code": r["code"], "title": r["title"], "category": r["category"],
                          "site_id": r["site_id"], "site_name": r["site_name"]},
            "version": {"id": r["version_id"], "version_no": r["version_no"], "approved_at": r["approved_at"],
                        "approved_by_name": r["approved_by_name"], "effective_from": r["effective_from"],
                        "effective_until": r["effective_until"]},
        })
    if not rows:
        answer["nothing"] = NOTHING
    return answer


async def relevant(db: AsyncSession, incident_types: list[str], site_id: Any, now: datetime,
                   allowed: list[str] | None) -> list[dict]:
    """The procedures in force for these kinds of incident at this site: the
    site's own before the ones for every site. Each with the whole text of the
    version in force."""
    kinds = [t for t in incident_types if t and TYPE.match(t)]
    if not kinds:
        return []
    params: dict = {"now": now, "kinds": kinds}
    # The incident's site decides which procedures apply; the reader's sites decide whether they may read them.
    where = "(d.site_id IS NULL OR d.site_id = CAST(:site AS uuid))" if site_id is not None else "d.site_id IS NULL"
    if site_id is not None:
        params["site"] = str(site_id)
    if allowed is not None:
        params["allowed_sites"] = list(allowed)
        where += " AND (d.site_id IS NULL OR d.site_id::text = ANY(:allowed_sites))"
    rows = (await db.execute(text(f"""
        SELECT d.id, d.code, d.title, d.category, d.site_id, s.name AS site_name, f.id AS version_id, f.version_no,
               f.body, f.decided_at AS approved_at, u.full_name AS approved_by_name, f.effective_from,
               f.effective_until, f.attachment_name,
               ARRAY(SELECT t.incident_type FROM sop_incident_types t
                      WHERE t.document_id = d.id AND t.incident_type = ANY(:kinds) ORDER BY 1) AS for_types
          FROM sop_documents d
          JOIN ({IN_FORCE}) f ON f.document_id = d.id
          LEFT JOIN sites s ON s.id = d.site_id
          LEFT JOIN users u ON u.id = f.decided_by_user_id
         WHERE {STANDING} AND {where}
           AND EXISTS (SELECT 1 FROM sop_incident_types t
                        WHERE t.document_id = d.id AND t.incident_type = ANY(:kinds))
         ORDER BY (d.site_id IS NULL), d.code
    """), params)).mappings().all()
    return [_procedure(r) for r in rows]


def _procedure(r: Mapping) -> dict:
    return {
        "id": r["id"], "code": r["code"], "title": r["title"], "category": r["category"], "site_id": r["site_id"],
        "site_name": r["site_name"], "for_types": list(r["for_types"]),
        "version": {"id": r["version_id"], "version_no": r["version_no"], "approved_at": r["approved_at"],
                    "approved_by_name": r["approved_by_name"], "effective_from": r["effective_from"],
                    "effective_until": r["effective_until"], "has_attachment": r["attachment_name"] is not None},
        "text": r["body"], "passages": passages(r["body"]),
    }
