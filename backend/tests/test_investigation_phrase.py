"""Smart investigation: a typed phrase is read by fixed rules, and says what it made of the words.

  A — When: every way of saying a period the vocabulary has
  B — What and where: kinds, events, severities, plates, names, places
  C — What it did not use, and what it refuses
  D — That nothing here is a language model

The claims, each with tests: a phrase becomes exactly the search a person could
have built from the filters; what was understood, assumed and not understood is
always reported; a word that narrows is never dropped silently; and a phrase
with nothing in it that was understood is refused rather than guessed at.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import investigation_phrase as ph
from app.services import investigation_sources as sources

SGT = ZoneInfo("Asia/Singapore")
#: A Tuesday, mid-morning.
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=SGT)
SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"

SITES = [ph.Place("s-a", "Factory A"), ph.Place("s-b", "Factory B")]
CAMERAS = [ph.Place("c-1", "North Gate"), ph.Place("c-2", "North Gate 2"), ph.Place("c-3", "Loading Bay")]


def parse(phrase: str, now: datetime = NOW, **places):
    return ph.parse(phrase, now=now, zone="Asia/Singapore", sites=places.get("sites", SITES),
                    cameras=places.get("cameras", CAMERAS))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SGT)


# ─── A. When ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, since, until", [
    ("alerts today", at(6, 0), NOW),
    ("alerts yesterday", at(5, 0), at(6, 0)),
    ("alerts last night", at(5, 18), at(6, 6)),
    ("alerts overnight", at(5, 18), at(6, 6)),
    ("alerts this morning", at(6, 6), NOW),
    ("alerts this week", at(5, 0), NOW),
    ("alerts last week", datetime(2026, 9, 28, tzinfo=SGT), at(5, 0)),
    ("alerts in the last 3 hours", at(6, 7), NOW),
    ("alerts past 2 days", at(4, 10), NOW),
    ("alerts last hour", at(6, 9), NOW),
    ("alerts past week", datetime(2026, 9, 29, 10, tzinfo=SGT), NOW),
    ("alerts on 5 oct", at(5, 0), at(6, 0)),
    ("alerts on October 5th", at(5, 0), at(6, 0)),
    ("alerts 2026-10-01", at(1, 0), at(2, 0)),
    ("alerts 3/10", at(3, 0), at(4, 0)),
    ("alerts from 1 oct to 3 oct", at(1, 0), at(4, 0)),
    ("alerts on monday", at(5, 0), at(6, 0)),
    ("alerts on tuesday", at(6, 0), NOW),
    ("alerts last tuesday", datetime(2026, 9, 29, tzinfo=SGT), datetime(2026, 9, 30, tzinfo=SGT)),
    ("alerts between 1 and 3:30", at(6, 1), at(6, 3, 30)),
    ("alerts between 1am and 3.30am", at(6, 1), at(6, 3, 30)),
    ("alerts from 22:00 to 02:00", at(5, 22), at(6, 2)),
    ("alerts after 22:00", at(5, 22), NOW),
    ("alerts since 8am", at(6, 8), NOW),
    ("alerts before 6am", at(6, 0), at(6, 6)),
    ("alerts around 9:30", at(6, 9), NOW),
    ("alerts at noon", at(5, 11, 30), at(5, 12, 30)),
    ("alerts last night between 1 and 3:30", at(6, 1), at(6, 3, 30)),
    ("alerts yesterday after 22:00", at(5, 22), at(6, 0)),
    ("alerts yesterday before 6am", at(5, 0), at(5, 6)),
    ("alerts after 22:00 before 2am", at(5, 22), at(6, 2)),
])
def test_every_way_of_saying_when(phrase, since, until):
    p = parse(phrase)
    assert (p.since, p.until) == (since, until), phrase
    assert p.kinds == ["ALERT"] and not p.not_understood, (phrase, p.not_understood)
    assert [u["field"] for u in p.understood].count("period") == 1 and not p.assumed


def test_a_time_just_after_midnight_means_the_night_that_has_just_been():
    just_after = datetime(2026, 10, 6, 0, 30, tzinfo=SGT)
    p = parse("alerts between 1 and 3:30", now=just_after)
    assert (p.since, p.until) == (at(5, 1), at(5, 3, 30)), "01:00 today has not happened yet"
    assert parse("alerts tonight", now=just_after).since == at(5, 18), "before dawn, tonight is the night still going"


def test_with_no_period_the_last_day_is_searched_and_it_says_so():
    p = parse("alerts at North Gate")
    assert (p.since, p.until) == (NOW - timedelta(hours=24), NOW)
    assert p.assumed == ["No period was given, so the last 24 hours were searched."]
    assert "period" not in [u["field"] for u in p.understood]


def test_a_period_that_cannot_be_is_refused_with_the_reason():
    for phrase, why in (("alerts tonight", "not happened yet"), ("alerts last 200 days", "at most 92 days"),
                        ("alerts after 25:00", "not a time of day"), ("alerts on 31 feb", "not a date")):
        with pytest.raises(ValueError, match=why):
            parse(phrase)
    assert sources.MAX_DAYS == 92 and sources.DEFAULT_HOURS == 24


def test_the_period_is_read_in_the_organisations_own_time_zone():
    utc = datetime(2026, 10, 5, 17, 0, tzinfo=ZoneInfo("UTC"))  # 01:00 on the 6th in Singapore
    assert ph.parse("alerts today", now=utc, zone="Asia/Singapore").since == at(6, 0)
    assert ph.parse("alerts today", now=utc, zone="UTC").since == datetime(2026, 10, 5, tzinfo=ZoneInfo("UTC"))


# ─── B. What and where ───────────────────────────────────────────────────────

def test_the_kinds_of_record_are_named_in_ordinary_words():
    for phrase, kinds in (
        ("vehicles today", ["PLATE_READ"]), ("number plates today", ["PLATE_READ"]),
        ("visitors and incidents today", ["INCIDENT", "VISITOR"]), ("door events today", ["ACCESS"]),
        ("watchlist matches today", ["FACE_MATCH"]), ("occurrence book today", ["OCCURRENCE"]),
        ("drone and alarm panel today", ["ALARM", "DRONE"]), ("patrol scans today", ["PATROL_SCAN"]),
        ("man down today", ["MAN_DOWN"]), ("sos today", ["MAN_DOWN"]), ("situations today", ["SITUATION"]),
        ("sensors today", ["SENSOR"]), ("detections today", ["DETECTION"]),
    ):
        p = parse(phrase)
        assert sorted(p.kinds) == kinds and not p.not_understood, (phrase, p.kinds, p.not_understood)
    assert set(ph.KIND_WORDS) == set(sources.KINDS), "a word for every kind, and no word for a kind that is not one"


def test_kinds_of_event_and_severities():
    p = parse("critical intrusion alerts and fire yesterday")
    assert (p.kinds, sorted(p.event_types), p.severities) == (["ALERT"], ["fire_smoke", "intrusion"], ["critical"])
    p = parse("doors forced open or denied last night")
    assert (p.kinds, sorted(p.event_types)) == (["ACCESS"], ["denied", "forced"])
    p = parse("high and medium alerts today")
    assert sorted(p.severities) == ["high", "medium"]
    assert set(ph.SEVERITY_WORDS) <= set(sources.SEVERITIES)


def test_a_number_plate_is_taken_when_it_is_called_one_or_written_like_one():
    for phrase, plate in (("plate SGX1234A today", "SGX1234A"), ("number plate sbf-4491 t yesterday", "SBF4491"),
                          ("where was sga1234b last night", "SGA1234B"), ("vehicles TN09AB1234 today", "TN09AB1234"),
                          ("plate SG*34A this week", "SG*34A"), ("registration no. ABC123 today", "ABC123")):
        assert parse(phrase).plate == plate, phrase
    for phrase in ("plate reads today", "alerts at cam12 today", "alerts gate2 today", "alerts at 3am"):
        assert _quiet(phrase).plate is None, f"'{phrase}' has no plate in it"


def _quiet(phrase: str):
    try:
        return parse(phrase)
    except ph.NotUnderstood:
        return ph.Parsed(since=NOW, until=NOW)


def test_the_organisations_own_places_longest_name_first():
    p = parse("vehicles at north gate 2 and the Loading Bay yesterday")
    assert sorted(p.camera_ids) == ["c-2", "c-3"], "North Gate 2 is not North Gate"
    assert parse("alerts at North Gate today").camera_ids == ["c-1"]
    p = parse("incidents at factory b this week")
    assert (p.site_ids, p.camera_ids) == (["s-b"], [])
    said = {u["field"]: u["as"] for u in p.understood}
    assert said["site"] == "the site Factory B"
    assert parse("alerts at North Gate today", cameras=[]).not_understood == ["north", "gate"], \
        "a place the caller cannot see is not a place they can name"


def test_a_name_is_taken_only_when_it_is_said_to_be_one():
    p = parse("visitors named Tan Wei Ming yesterday")
    assert (p.person, p.kinds) == ("Tan Wei Ming", ["VISITOR"]) and not p.not_understood
    assert parse('access called "raj kumar" today').person == "raj kumar"
    assert parse("visitors named Siti binte Ahmad today").person == "Siti binte Ahmad"
    p = parse("visitors Tan Wei Ming yesterday")
    assert p.person is None and p.not_understood == ["tan", "wei", "ming"], "not said to be a name, so not guessed"


def test_words_in_quotes_are_looked_for_as_written():
    p = parse('occurrence book "blue lorry" this week')
    assert (p.text, p.kinds) == ("blue lorry", ["OCCURRENCE"]) and not p.not_understood


# ─── C. What it did not use, and what it refuses ─────────────────────────────

def test_what_was_made_of_the_words_is_reported_word_for_word():
    p = parse("Show me critical vehicles at North Gate last night please")
    fields = {u["field"]: (u["words"], u["as"]) for u in p.understood}
    assert fields["kind"] == ("vehicles", "PLATE_READ")
    assert fields["severity"] == ("critical", "critical")
    assert fields["camera"] == ("North Gate", "the camera North Gate")
    assert fields["period"] == ("last night", "yesterday 18:00 to today 06:00")
    assert not p.not_understood and not p.assumed


def test_a_word_that_was_not_used_is_listed_and_only_asking_words_are_dropped():
    p = parse("show me all the suspicious people near the fence yesterday")
    assert p.not_understood == ["suspicious", "people", "fence"]
    assert (p.since, p.kinds) == (at(5, 0), [])
    for word in ("people", "person", "guard", "gate", "zone", "suspicious", "after", "risk"):
        assert word not in ph.NOISE, f"'{word}' narrows a search and must not vanish"
    assert {"show", "me", "the", "find", "please"} <= ph.NOISE


def test_a_phrase_with_nothing_understood_is_refused_not_guessed_at():
    with pytest.raises(ph.NotUnderstood) as refused:
        parse("anything odd going on with the contractors")
    assert refused.value.words == ["odd", "going", "contractors"]
    for empty in ("", "   ", "show me everything"):
        with pytest.raises(ph.NotUnderstood):
            parse(empty)
    with pytest.raises(ValueError, match="at most 300"):
        parse("alerts " * 60)


def test_a_phrase_makes_only_what_the_filters_could_have_made():
    p = parse('critical vehicles SGA1234B at North Gate named Lim "white van" last night')
    made = {k for k, v in vars(p).items() if v and k not in ("understood", "assumed", "not_understood")}
    assert made <= {"since", "until", "kinds", "site_ids", "camera_ids", "event_types", "severities", "plate",
                    "person", "text"}
    assert made <= set(vars(sources.Query(since=NOW, until=NOW))), "every field a phrase sets is a filter"
    assert "staff_user_id" not in vars(p) and "risk_levels" not in vars(p), \
        "who a member of staff is, and risk, are chosen from the filters, not typed"


def test_the_screen_is_told_what_can_be_said():
    v = ph.vocabulary()
    assert set(v["kinds"]) == set(sources.KINDS) and set(v["event_types"]) == set(ph.EVENT_WORDS)
    assert "not understood" in v["limits"]
    for example in v["periods"]:
        assert parse(f"alerts {example}", now=datetime(2026, 10, 7, 23, 0, tzinfo=SGT)).kinds == ["ALERT"], example
    for example in v["plates"]:
        assert parse(f"{example} today").plate
    for example in v["people"]:
        assert parse(f"visitors {example} today").person


# ─── D. Nothing here is a language model ─────────────────────────────────────

def test_the_phrase_reader_calls_nothing_and_knows_no_model():
    for name in ("investigation_phrase.py", "investigation_sources.py"):
        code = (SERVICES / name).read_text(encoding="utf-8")
        imports = re.findall(r"^(?:from|import)\s+([\w.]+)", code, re.M)
        for module in imports:
            assert module.split(".")[0] in {"__future__", "re", "uuid", "dataclasses", "datetime", "typing",
                                            "zoneinfo", "sqlalchemy", "app"}, f"{name} imports {module}"
        for word in ("openai", "anthropic", "ollama", "langchain", "httpx", "requests", "embedding"):
            assert word not in code.lower().replace("no language model", ""), f"{name} mentions {word}"
    phrase = (SERVICES / "investigation_phrase.py").read_text(encoding="utf-8")
    assert "sqlalchemy" not in phrase and "AsyncSession" not in phrase, "the reader is pure: no database"
