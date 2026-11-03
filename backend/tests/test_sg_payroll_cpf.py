"""Singapore payroll: CPF from CPF Board's published tables, PR graduated rates, the Additional Wage ceiling, MOM's
PWM schedule, and the new-employee form's CPF preview (migration 0175; Phase 1 of closing the known limits,
docs/plans/2026-11-03-known-limits-programme-and-sg-payroll-design.md).

The figures in section A are CPF's own, typed from the published rate tables - the "Max." contribution on Ordinary
Wages at the ceiling, for every row - so the rates loaded by the migration are held to the documents rather than to
themselves.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.services import cpf, pwm
from tests.test_shifts import _admin_engine

D = Decimal

# CPF Board, "CPF Contribution Rate Table": per scheme and age band, the published Max. total contribution and
# Max. employee's share on Ordinary Wages (wages above $750, OW at the ceiling).
PUBLISHED_MAX = {
    date(2026, 1, 1): {
        "full":        {0: (2960, 1600), 55: (2720, 1440), 60: (2000, 1000), 65: (1320, 600), 70: (1000, 400)},
        "graduated_1": {0: (720, 400), 55: (720, 400), 60: (680, 400), 65: (680, 400)},
        "graduated_2": {0: (1920, 1200), 55: (1480, 1000), 60: (880, 600), 65: (680, 400)},
        "fg_1":        {0: (1760, 400), 55: (1680, 400), 60: (1400, 400), 65: (1120, 400), 70: (1000, 400)},
        "fg_2":        {0: (2560, 1200), 55: (2280, 1000), 60: (1600, 600), 65: (1120, 400), 70: (1000, 400)},
    },
    date(2025, 1, 1): {
        "full":        {0: (2738, 1480), 55: (2405, 1258), 60: (1739, 851), 65: (1221, 555), 70: (925, 370)},
        "graduated_1": {0: (666, 370), 55: (666, 370), 60: (629, 370), 65: (629, 370)},
        "graduated_2": {0: (1776, 1110), 55: (1369, 925), 60: (814, 555), 65: (629, 370)},
        "fg_1":        {0: (1628, 370), 55: (1517, 370), 60: (1258, 370), 65: (1036, 370), 70: (925, 370)},
        "fg_2":        {0: (2368, 1110), 55: (2072, 925), 60: (1443, 555), 65: (1036, 370), 70: (925, 370)},
    },
}


async def _rates(on: date) -> cpf.Rates | None:
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        rates = await cpf.load_rates(s, on)
    await engine.dispose()
    return rates


# ─── A. CPF's own figures ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("effective", sorted(PUBLISHED_MAX))
async def test_every_published_row_gives_cpf_s_own_maximum(effective):
    rates = await _rates(effective)
    assert rates is not None and rates.effective_from == effective
    assert rates.ow_ceiling == {date(2025, 1, 1): D(7400), date(2026, 1, 1): D(8000)}[effective]
    assert rates.annual_wage_ceiling == D(102000)
    assert set(rates.bands) == set(PUBLISHED_MAX[effective])
    for scheme, bands in PUBLISHED_MAX[effective].items():
        assert set(rates.bands[scheme]) == set(bands), scheme
        for age_above, (total_max, employee_max) in bands.items():
            er, ee = rates.bands[scheme][age_above]
            paid = cpf.contribution(ordinary_wage=D(20000), additional_wage=D(0), employer_rate=er,
                                    employee_rate=ee, ow_ceiling=rates.ow_ceiling)
            assert (paid.total, paid.employee) == (D(total_max), D(employee_max)), (effective, scheme, age_above)
            assert paid.employer == D(total_max - employee_max)
            # The $50-$500 rate is the employer's (CPF rounds 50 cents up: 12.5% x $500 = $62.50 is $63):
            assert cpf.contribution(ordinary_wage=D(500), additional_wage=D(0), employer_rate=er, employee_rate=ee,
                                    ow_ceiling=rates.ow_ceiling).total == (er * 500).quantize(D(1), ROUND_HALF_UP)


def test_the_wage_bands_and_cpf_s_rounding():
    er, ee, ceiling = D("0.17"), D("0.20"), D(8000)   # 2026, citizen, 55 and below

    def paid(ow, aw=D(0), left=None):
        return cpf.contribution(ordinary_wage=D(ow), additional_wage=D(aw), employer_rate=er, employee_rate=ee,
                                ow_ceiling=ceiling, aw_ceiling_left=left)

    assert (paid(50).total, paid(50).employee) == (D(0), D(0)), "$50 or less: nil"
    assert (paid(51).total, paid(51).employee) == (D(9), D(0)), "17% x $51 = $8.67, to the nearest dollar"
    assert (paid(500).total, paid(500).employee) == (D(85), D(0)), "up to $500: the employer only"
    p = paid(600)   # 17% x 600 + 0.6 x (600 - 500)
    assert (p.total, p.employee, p.employer) == (D(162), D(60), D(102))
    p = paid(750)
    assert (p.total, p.employee) == (D(278), D(150)), "17% x 750 = 127.50 + 150, rounded: the same as 37% x 750"
    p = paid(D("1234.56"))   # 37% = 456.787 -> 457; employee 20% = 246.912 -> down to 246
    assert (p.total, p.employee, p.employer) == (D(457), D(246), D(211))
    p = paid(9000)
    assert (p.total, p.employee, p.ow_subject) == (D(2960), D(1600), D(8000)), "OW above the ceiling is not counted"
    p = paid(8000, aw=10000, left=D(6000))
    assert (p.aw_subject, p.total, p.employee) == (D(6000), D(5180), D(2800)), "AW only up to what is left"


def test_a_new_age_band_starts_the_month_after_the_birthday():
    born = date(1971, 3, 15)   # 55 on 15 March 2026
    assert cpf.age_band(born, 2026, 3) == 0
    assert cpf.age_band(born, 2026, 4) == 55
    assert cpf.age_band(date(1956, 1, 1), 2026, 2) == 70
    assert cpf.age_band(date(1956, 1, 1), 2026, 2, bands=(0, 55, 60, 65)) == 65, "the graduated tables stop at 65"


def test_the_pr_year_changes_the_month_after_each_anniversary():
    since = date(2026, 1, 15)   # CPF's own example
    assert cpf.pr_year(since, 2026, 1) == 1
    assert cpf.pr_year(since, 2027, 1) == 1
    assert cpf.pr_year(since, 2027, 2) == 2
    assert cpf.pr_year(since, 2028, 1) == 2
    assert cpf.pr_year(since, 2028, 2) == 3


@pytest.mark.asyncio
async def test_which_table_applies_and_what_is_never_guessed():
    rates = await _rates(date(2026, 10, 1))

    def who(**kw):
        base = dict(work_pass_type="citizen", date_of_birth=date(1990, 5, 1), pr_since=None, cpf_rate_type=None,
                    year=2026, month=10, rates=rates)
        return cpf.who_pays(**{**base, **kw})

    assert who(work_pass_type="wp") == cpf.Who(eligible=False)
    assert who().scheme == "full" and who().age_above == 0
    assert "date of birth" in who(date_of_birth=None).note
    assert "PR since" in who(work_pass_type="pr").note
    assert who(work_pass_type="pr", pr_since=date(2026, 3, 10)).scheme == "graduated_1"
    assert who(work_pass_type="pr", pr_since=date(2025, 3, 10)).scheme == "graduated_2"
    assert who(work_pass_type="pr", pr_since=date(2025, 3, 10), cpf_rate_type="fg").scheme == "fg_2"
    assert who(work_pass_type="pr", pr_since=date(2026, 3, 10), cpf_rate_type="full").scheme == "full"
    assert who(work_pass_type="pr", pr_since=date(2020, 3, 10), cpf_rate_type="fg").scheme == "full", "3rd year on"
    assert "No CPF rate table" in who(rates=None).note
    assert await _rates(date(2024, 12, 1)) is None, "no table is on file before 2025: refused, not guessed"


# ─── B. A payroll run ─────────────────────────────────────────────────────────

ADMIN = 2


async def _sql(statements: list[tuple[str, dict]]):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        for sql, params in statements:
            await s.execute(text(sql), params)
        await s.commit()
    await engine.dispose()


async def _org() -> dict:
    from app.core.security import create_access_token, hash_password

    tid, admin = uuid.uuid4(), uuid.uuid4()
    await _sql([
        ("INSERT INTO tenants (id, name, slug) VALUES (:id, 'SG Payroll', :s)", {"id": tid, "s": f"sgpay-{tid.hex[:8]}"}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) VALUES (:id, :t, 2, :e, :pw)",
         {"id": admin, "t": tid, "e": f"admin-{admin.hex[:6]}@test.local", "pw": hash_password("x")}),
    ])
    return {"tid": tid, "token": create_access_token(str(admin), str(tid), ADMIN)}


async def _guard(org: dict, name: str, **cols) -> str:
    gid = uuid.uuid4()
    fields = {"full_name": name, "employment_type": "full_time", **cols}
    names = ", ".join(fields)
    values = ", ".join(f":{k}" for k in fields)
    await _sql([(f"INSERT INTO users (id, tenant_id, role_id, email, hashed_password, {names}) "
                 f"VALUES (:id, :t, 5, :e, 'x', {values})",
                 {"id": gid, "t": org["tid"], "e": f"g-{gid.hex[:8]}@test.local", **fields})])
    return str(gid)


def _client(org: dict) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    c.headers["Authorization"] = f"Bearer {org['token']}"
    return c


def _slip(run: dict, gid: str) -> dict:
    return next(p for p in run["payslips"] if str(p["guard_user_id"]) == gid)


@pytest.mark.asyncio
async def test_a_month_s_payroll_for_a_citizen_a_first_year_pr_a_58_year_old_and_a_foreigner():
    org = await _org()
    citizen = await _guard(org, "Citizen", work_pass_type="citizen", date_of_birth=date(1990, 5, 1),
                           monthly_salary=D(3000))
    pr1 = await _guard(org, "PR year 1", work_pass_type="pr", date_of_birth=date(1990, 5, 1),
                       pr_since=date(2026, 3, 10), monthly_salary=D(3000))
    senior = await _guard(org, "Fifty-eight", work_pass_type="citizen", date_of_birth=date(1968, 2, 1),
                          monthly_salary=D(3000))
    foreign = await _guard(org, "Work permit", work_pass_type="wp", date_of_birth=date(1990, 5, 1),
                           monthly_salary=D(2000))
    no_dob = await _guard(org, "No birthday", work_pass_type="citizen", monthly_salary=D(3000))
    async with _client(org) as c:
        r = await c.post("/api/v1/payroll/runs", json={"period_start": "2026-10-01", "period_end": "2026-10-31"})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["cpf_rates_from"] == "2026-01-01"

    def money(p, *keys):
        return tuple(D(str(p[k])) for k in keys)

    assert money(_slip(run, citizen), "cpf_employee", "cpf_employer", "net_pay") == (D(600), D(510), D(2400))
    assert _slip(run, citizen)["cpf_scheme"] == "full"
    assert money(_slip(run, pr1), "cpf_employee", "cpf_employer") == (D(150), D(120)), "9%: employer 4%, employee 5%"
    assert _slip(run, pr1)["cpf_scheme"] == "graduated_1"
    assert money(_slip(run, senior), "cpf_employee", "cpf_employer") == (D(540), D(480)), "2026: 34% = 16% + 18%"
    assert _slip(run, senior)["cpf_age_above"] == 55
    assert money(_slip(run, foreign), "cpf_employee", "cpf_employer") == (D(0), D(0))
    assert _slip(run, foreign)["cpf_note"] is None
    assert money(_slip(run, no_dob), "cpf_employee") == (D(0),)
    assert "date of birth" in _slip(run, no_dob)["cpf_note"]
    assert any("No birthday: CPF not worked out" in w for w in run["warnings"])


@pytest.mark.asyncio
async def test_a_bonus_on_a_draft_run_after_recalculation_and_a_finalised_run_is_never_changed():
    org = await _org()
    gid = await _guard(org, "Bonus", work_pass_type="citizen", date_of_birth=date(1990, 5, 1), monthly_salary=D(3000))
    async with _client(org) as c:
        run = (await c.post("/api/v1/payroll/runs",
                            json={"period_start": "2026-10-01", "period_end": "2026-10-31"})).json()
        line = await c.post(f"/api/v1/payroll/runs/{run['id']}/additional-wages",
                            json={"guard_user_id": gid, "kind": "bonus", "amount": "5000", "note": "Year-end"})
        assert line.status_code == 201, line.text
        assert (await c.get(f"/api/v1/payroll/runs/{run['id']}")).json()["needs_recalculation"] is True
        again = await c.post(f"/api/v1/payroll/runs/{run['id']}/recalculate")
        assert again.status_code == 200, again.text
        slip = _slip(again.json(), gid)
        # 37% x 3000 + 37% x 5000 = 2960; employee 20% x 8000 = 1600; the year's AW ceiling is far from reached.
        assert (D(str(slip["gross_pay"])), D(str(slip["cpf_employee"])), D(str(slip["cpf_employer"]))) == \
            (D(8000), D(1600), D(1360))
        assert D(str(slip["aw_subject_to_cpf"])) == D(5000)
        assert (await c.get(f"/api/v1/payroll/runs/{run['id']}")).json()["needs_recalculation"] is False

        removed = await c.delete(f"/api/v1/payroll/runs/{run['id']}/additional-wages/{line.json()['id']}")
        assert removed.status_code == 200
        assert (await c.get(f"/api/v1/payroll/runs/{run['id']}")).json()["needs_recalculation"] is True, \
            "removing the only line still leaves the payslips out of date"
        await c.post(f"/api/v1/payroll/runs/{run['id']}/recalculate")

        assert (await c.put(f"/api/v1/payroll/runs/{run['id']}/finalize")).status_code == 200
        before = (await c.get(f"/api/v1/payroll/runs/{run['id']}")).json()["payslips"]
        assert (await c.post(f"/api/v1/payroll/runs/{run['id']}/recalculate")).status_code == 409
        assert (await c.post(f"/api/v1/payroll/runs/{run['id']}/additional-wages",
                             json={"guard_user_id": gid, "kind": "aws", "amount": "100"})).status_code == 409
        assert (await c.get(f"/api/v1/payroll/runs/{run['id']}")).json()["payslips"] == before


@pytest.mark.asyncio
async def test_the_year_s_additional_wage_ceiling_across_two_runs():
    org = await _org()
    gid = await _guard(org, "High earner", work_pass_type="citizen", date_of_birth=date(1990, 5, 1),
                       monthly_salary=D(9000))
    async with _client(org) as c:
        jan = (await c.post("/api/v1/payroll/runs",
                            json={"period_start": "2026-01-01", "period_end": "2026-01-31"})).json()
        await c.post(f"/api/v1/payroll/runs/{jan['id']}/additional-wages",
                     json={"guard_user_id": gid, "kind": "bonus", "amount": "20000"})
        slip = _slip((await c.post(f"/api/v1/payroll/runs/{jan['id']}/recalculate")).json(), gid)
        # OW at the $8,000 ceiling for the 12 months = 96,000, so 6,000 of the bonus is subject to CPF.
        assert (D(str(slip["aw_ceiling_left"])), D(str(slip["aw_subject_to_cpf"]))) == (D(6000), D(6000))
        assert D(str(slip["cpf_employee"])) == D(1600 + 1200)
        await c.put(f"/api/v1/payroll/runs/{jan['id']}/finalize")

        feb = (await c.post("/api/v1/payroll/runs",
                            json={"period_start": "2026-02-01", "period_end": "2026-02-28"})).json()
        await c.post(f"/api/v1/payroll/runs/{feb['id']}/additional-wages",
                     json={"guard_user_id": gid, "kind": "incentive", "amount": "3000"})
        slip = _slip((await c.post(f"/api/v1/payroll/runs/{feb['id']}/recalculate")).json(), gid)
        assert D(str(slip["aw_subject_to_cpf"])) == D(0), "January's bonus used the year's ceiling"
        await c.put(f"/api/v1/payroll/runs/{feb['id']}/finalize")

        check = (await c.get("/api/v1/payroll/aw-ceiling-check", params={"year": 2026})).json()
    me = next(e for e in check["employees"] if e["guard_user_id"] == gid)
    # Only two months are finalised: OW 16,000 leaves a ceiling of 86,000, so all 23,000 was due - the check says
    # the AW contributed on is under by 17,000 (the year-end check reports; it changes nothing).
    assert (D(str(me["aw_ceiling"])), D(str(me["aw_contributed_on"])), me["verdict"]) == (D(86000), D(6000), "under")


@pytest.mark.asyncio
async def test_mom_s_floors_by_arrangement_and_hours():
    org = await _org()
    outsourced = await _guard(org, "Outsourced", pwm_grade="security_officer", monthly_salary=D(3000))
    part_timer = await _guard(org, "Part-timer", pwm_grade="security_officer", employment_type="part_time",
                              hourly_rate=D(11))
    in_house = await _guard(org, "In-house", pwm_grade="security_officer", employment_arrangement="in_house",
                            monthly_salary=D(2400))
    # MOM leaves an in-house Senior Security Supervisor to the market. (Not a Chief Security Officer here: the PWM
    # floor tests write a test floor for that grade into the same shared table.)
    unset = await _guard(org, "In-house senior supervisor", pwm_grade="senior_security_supervisor",
                         employment_arrangement="in_house", monthly_salary=D(2000))
    async with _client(org) as c:
        report = (await c.get("/api/v1/payroll/pwm-compliance", params={"as_of": "2026-10-01"})).json()
        run = (await c.post("/api/v1/payroll/runs",
                            json={"period_start": "2026-10-01", "period_end": "2026-10-31"})).json()
    by = {g["user_id"]: g for g in report["guards"]}
    assert (by[outsourced]["verdict"], D(str(by[outsourced]["floor_applied"])), by[outsourced]["basis"]) == \
        ("below_floor", D(3090), "monthly_basic")
    assert by[outsourced]["source"].startswith("MOM, Progressive Wage Model")
    assert (by[part_timer]["verdict"], D(str(by[part_timer]["floor_applied"])), by[part_timer]["basis"]) == \
        ("below_floor", D("11.80"), "hourly_basic"), "MOM's own hourly floor, not a monthly one divided"
    assert (by[in_house]["verdict"], by[in_house]["basis"]) == ("not_assessed", "monthly_gross"), \
        "an in-house floor is on gross wage, which only a payroll run knows"
    assert (by[unset]["verdict"], by[unset]["floor_applied"]) == ("not_assessed", None), "MOM sets no floor for it"
    flagged = {e["user_id"]: e for e in run["pwm_exceptions"]}
    assert set(flagged) == {outsourced, part_timer, in_house}, "the run judges the in-house guard on gross: 2,400"
    assert D(str(flagged[in_house]["floor_applied"])) == D(2475)
    assert pwm.in_house_hourly(D(2475)) == D("12.98"), "monthly x 12 / (44 x 52)"


@pytest.mark.asyncio
async def test_the_form_s_preview_is_the_payslip_s_cpf_and_an_employee_is_created_with_residency():
    org = await _org()
    async with _client(org) as c:
        p = (await c.post("/api/v1/payroll/cpf-preview", json={
            "work_pass_type": "citizen", "date_of_birth": "1990-05-01", "monthly_salary": "3000",
            "month": "2026-10-01"})).json()
        assert (D(str(p["employee_cpf"])), D(str(p["employer_cpf"])), D(str(p["take_home"]))) == \
            (D(600), D(510), D(2400))
        assert (p["employee_percent"], p["employer_percent"], p["total_percent"]) == ("20%", "17%", "37%")
        foreigner = (await c.post("/api/v1/payroll/cpf-preview",
                                  json={"work_pass_type": "sp", "monthly_salary": "4000"})).json()
        assert foreigner["eligible"] is False and "No CPF" in foreigner["note"]
        pr = (await c.post("/api/v1/payroll/cpf-preview", json={
            "work_pass_type": "pr", "date_of_birth": "1990-05-01", "pr_since": "2026-03-10", "cpf_rate_type": "fg",
            "monthly_salary": "3000", "month": "2026-10-01"})).json()
        assert (pr["scheme"], pr["employer_percent"], pr["employee_percent"]) == ("fg_1", "17%", "5%")

        made = await c.post("/api/v1/users", json={
            "email": f"pr-{uuid.uuid4().hex[:6]}@test.local", "password": "Tq7!mR2#vLx9$kE4", "role_id": 5,
            "full_name": "New PR", "work_pass_type": "pr", "date_of_birth": "1990-05-01",
            "pr_since": "2026-03-10", "cpf_rate_type": "fg", "monthly_salary": 3000,
            "employment_arrangement": "outsourced"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["work_pass_type"], body["pr_since"], body["cpf_rate_type"], float(body["monthly_salary"])) == \
        ("pr", "2026-03-10", "fg", 3000.0)
