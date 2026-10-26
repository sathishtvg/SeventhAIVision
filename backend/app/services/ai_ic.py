"""Reading a Singapore IC (NRIC) or FIN card from photos, on our own server.

THE PHOTOS ARE NEVER KEPT. They are read in memory and dropped as soon as the
text is out: never written to disk, never stored in the database, never sent to
any model, hosted or not. The owner's decision, 26 October 2026.

THE TEXT READER IS RapidOCR (`rapidocr-onnxruntime`, Apache-2.0): free, running
on the onnxruntime the image already has, its models inside the package (nothing
is downloaded when it runs). It reads printed card text well, but tends to drop
the spaces between words; they are put back where the gap between letters is
clearly wider than a letter, and the person checks every field before anything
is created.

THE NRIC/FIN IS CHECKED against its check letter (S/T and F/G series). A number
that fails is still shown, marked, so the person looks again.

WHAT IS READ: NRIC/FIN, name, date of birth, sex, race or nationality, country of
birth, and the address (back of the NRIC). Anything not found is listed as not
read, for the assistant to ask for.
"""
from __future__ import annotations

import asyncio
import re
import statistics
from dataclasses import dataclass, field
from datetime import date

NRIC = re.compile(r"[STFGM]\d{7}[A-Z]")
DATE = re.compile(r"(\d{2})[-/.](\d{2})[-/.](\d{4})")
POSTAL = re.compile(r"SINGAPORE\s*(\d{6})")
_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_LETTERS = {"ST": "JZIHGFEDCBA", "FG": "XWUTRQPNMLK"}
#: A gap between two letters wider than this share of the usual letter width is a space.
SPACE_GAP = 0.8
FIELDS = ("nric_fin", "full_name", "date_of_birth", "sex", "race", "nationality", "country_of_birth", "address")


def check_letter_ok(value: str) -> bool | None:
    """True or False for the S/T/F/G series; None for M (its table is not applied here)."""
    if not NRIC.fullmatch(value):
        return False
    prefix, digits, letter = value[0], [int(c) for c in value[1:8]], value[8]
    if prefix == "M":
        return None
    total = sum(d * w for d, w in zip(digits, _WEIGHTS)) + (4 if prefix in "TG" else 0)
    table = _LETTERS["ST"] if prefix in "ST" else _LETTERS["FG"]
    return table[total % 11] == letter


def spaced(text: str, boxes: list | None) -> str:
    """Put back the spaces the reader dropped, from the gaps between its letter boxes."""
    if not boxes or len(boxes) != len(text):
        return text
    lefts = [min(p[0] for p in b) for b in boxes]
    rights = [max(p[0] for p in b) for b in boxes]
    widths = [r - l for l, r in zip(lefts, rights) if r > l]
    if not widths:
        return text
    usual = statistics.median(widths)
    out = [text[0]]
    for i in range(1, len(text)):
        if lefts[i] - rights[i - 1] > SPACE_GAP * usual and text[i] != " " and text[i - 1] != " ":
            out.append(" ")
        out.append(text[i])
    return "".join(out)


def lines_of(result) -> list[str]:
    """The reader's result, top to bottom, left to right, each with its spaces put back."""
    rows = []
    for item in result or []:
        box, text = item[0], item[1]
        words = item[3] if len(item) > 3 else None
        top, left = min(p[1] for p in box), min(p[0] for p in box)
        rows.append((top, left, spaced(str(text).strip(), words)))
    rows.sort(key=lambda r: (round(r[0] / 12), r[1]))
    return [r[2] for r in rows if r[2]]


def _key(line: str) -> str:
    return re.sub(r"[^A-Z]", "", line.upper())


def _iso(line: str) -> str | None:
    m = DATE.search(line)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


@dataclass
class Reading:
    fields: dict = field(default_factory=dict)
    unread: list[str] = field(default_factory=list)
    check_letter_ok: bool | None = None


def parse(front: list[str], back: list[str]) -> Reading:
    """The card's fields from its lines (front, then back)."""
    out: dict = {}
    lines = front + back
    for line in lines:
        m = NRIC.search(line.replace(" ", "").upper().replace("O", "0").replace("N0", "NO"))
        if m and "nric_fin" not in out:
            out["nric_fin"] = m.group(0)

    def after(label: str, within: list[str]) -> str | None:
        for i, line in enumerate(within):
            if _key(line) == label and i + 1 < len(within):
                return within[i + 1].strip()
        return None

    name = after("NAME", front)
    if name:
        out["full_name"] = re.sub(r"\s+", " ", name).strip()
    for label, key in (("RACE", "race"), ("NATIONALITY", "nationality"), ("COUNTRYPLACEOFBIRTH", "country_of_birth")):
        value = after(label, front)
        if value:
            out[key] = value.strip()
    for line in front:
        if "date_of_birth" not in out and (iso := _iso(line)):
            out["date_of_birth"] = iso
        if "sex" not in out and line.strip().upper() in ("M", "F"):
            out["sex"] = line.strip().upper()
    for line in front:
        tail = re.search(r"\b([MF])\s*$", line.strip())
        if "sex" not in out and DATE.search(line) and tail:
            out["sex"] = tail.group(1)
    # The address: the lines after "Address" on the back, up to and including the postal line.
    keys = [_key(x) for x in back]
    if "ADDRESS" in keys:
        part = []
        for line in back[keys.index("ADDRESS") + 1:]:
            part.append(line.strip())
            if POSTAL.search(line.upper().replace(" ", "")) or POSTAL.search(line.upper()):
                break
        if part:
            address = " ".join(part)
            address = re.sub(r"SINGAPORE\s*(\d{6})", r"SINGAPORE \1", address)
            out["address"] = address
    reading = Reading(fields=out, unread=[f for f in FIELDS if f not in out])
    if "nric_fin" in out:
        reading.check_letter_ok = check_letter_ok(out["nric_fin"])
    return reading


_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # here: only a server that reads ICs loads it

        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_lines(image: bytes) -> list[str]:
    """One photo's lines. Tests replace this; it is the only place the reader runs."""
    result, _ = _engine()(image, return_word_box=True)
    return lines_of(result)


async def read(front: bytes, back: bytes | None) -> Reading:
    """Read the card; the photos go out of scope with this call and are not kept anywhere."""
    front_lines = await asyncio.to_thread(ocr_lines, front)
    back_lines = await asyncio.to_thread(ocr_lines, back) if back else []
    return parse(front_lines, back_lines)


def masked(value: str) -> str:
    return "*" * max(0, len(value) - 4) + value[-4:]
