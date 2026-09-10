"""The pricing engine, where being approximately right is being wrong.

Money is the one part of this product where a small error is not a small
problem: a bill a cent out is a customer who stops trusting the whole document,
and a bill that charges for an allowance they were promised is the oldest
complaint in the business.

These are pure — no database, no fixtures — because the engine is pure. Given a
plan, usage and modules it returns lines and a total, and that is testable at
the level it actually matters.
"""
from decimal import Decimal

import pytest

from app.services.pricing import Module, Plan, Usage, money, quote

STARTER = Plan(
    name="Starter",
    base_price=Decimal("500"),
    included_cameras=50, included_sites=5, included_users=25,
    included_storage_gb=500,
    price_per_camera=Decimal("5"),
    price_per_site=Decimal("50"),
    price_per_user=Decimal("2"),
    price_per_gb=Decimal("0.10"),
)


# ─── Rounding ────────────────────────────────────────────────────────────────

def test_money_rounds_half_up_not_half_to_even():
    """Python rounds half to even — round(0.5) is 0 — which is right for
    statistics and wrong for money. Half-up is what an accountant expects, and
    a bill that rounds the customer's way half the time is a discrepancy
    somebody reconciles later."""
    assert money("0.005") == Decimal("0.01")
    assert money("0.015") == Decimal("0.02")
    assert money("2.345") == Decimal("2.35")


def test_the_engine_does_not_use_floats():
    """0.1 + 0.2 is not 0.3 in binary floating point. Three modules at $0.10 a
    camera must come to exactly what they come to."""
    q = quote(
        Plan(name="Float trap", base_price=Decimal("0.10")),
        Usage(cameras=1),
        [Module("a", "A", "per_camera", Decimal("0.10")),
         Module("b", "B", "per_camera", Decimal("0.10"))],
    )
    assert q.subtotal == Decimal("0.30")
    assert isinstance(q.total_amount, Decimal)


# ─── Allowances ──────────────────────────────────────────────────────────────

def test_usage_inside_the_allowance_costs_nothing_extra():
    q = quote(STARTER, Usage(cameras=40, sites=3, users=10, storage_gb=Decimal("200")))
    assert q.subtotal == Decimal("500.00")
    assert [line.kind for line in q.lines] == ["base"]


def test_only_the_overage_is_charged():
    """A plan including fifty cameras, used for sixty, bills ten. Charging all
    sixty is the oldest billing complaint there is."""
    q = quote(STARTER, Usage(cameras=60, sites=5, users=25))
    camera_line = next(line for line in q.lines if line.kind == "camera")
    assert camera_line.quantity == Decimal(10)
    assert camera_line.line_total == Decimal("50.00")
    assert q.subtotal == Decimal("550.00")


def test_being_under_the_allowance_never_credits_the_customer():
    """A negative quantity reaching an invoice would reduce the bill, which is
    not a discount anyone agreed to."""
    q = quote(STARTER, Usage(cameras=1, sites=1, users=1))
    assert all(line.quantity >= 0 for line in q.lines)
    assert q.subtotal == Decimal("500.00")


def test_every_unit_type_is_charged_above_its_own_allowance():
    q = quote(STARTER, Usage(cameras=55, sites=7, users=30,
                             storage_gb=Decimal("600")))
    by_kind = {line.kind: line for line in q.lines}
    assert by_kind["camera"].line_total == Decimal("25.00")    # 5 × $5
    assert by_kind["site"].line_total == Decimal("100.00")     # 2 × $50
    assert by_kind["user"].line_total == Decimal("10.00")      # 5 × $2
    assert by_kind["storage"].line_total == Decimal("10.00")   # 100 GB × $0.10
    assert q.subtotal == Decimal("645.00")


# ─── Modules ─────────────────────────────────────────────────────────────────

def test_a_per_camera_module_scales_with_cameras():
    q = quote(Plan(name="Base", base_price=Decimal("0")), Usage(cameras=20),
              [Module("lpr", "AI LPR", "per_camera", Decimal("5"))])
    line = next(line for line in q.lines if line.module_code == "lpr")
    assert line.quantity == Decimal(20)
    assert line.line_total == Decimal("100.00")


def test_a_flat_module_does_not_scale():
    q = quote(Plan(name="Base", base_price=Decimal("0")), Usage(cameras=400),
              [Module("windows", "Windows Command Centre", "flat", Decimal("150"))])
    line = next(line for line in q.lines if line.module_code == "windows")
    assert line.quantity == Decimal(1)
    assert line.line_total == Decimal("150.00")


def test_an_included_module_still_appears_at_zero():
    """A customer reading their bill should see everything they have. A module
    that silently vanishes looks like one they are not entitled to."""
    q = quote(Plan(name="Base", base_price=Decimal("100")), Usage(cameras=10),
              [Module("vms", "Video Management", "included", Decimal("0"))])
    line = next(line for line in q.lines if line.module_code == "vms")
    assert line.line_total == Decimal("0.00")
    assert "included" in line.description


def test_modules_combine_with_per_unit_charges():
    """§13's requirement: base plus units plus modules, all at once."""
    q = quote(
        STARTER,
        Usage(cameras=60, sites=5, users=25),
        [Module("lpr", "AI LPR", "per_camera", Decimal("5")),
         Module("windows", "Windows Command Centre", "flat", Decimal("150"))],
    )
    # 500 base + 50 camera overage + 300 LPR (60 cameras) + 150 Windows
    assert q.subtotal == Decimal("1000.00")


# ─── Discount and tax ────────────────────────────────────────────────────────

def test_tax_is_charged_on_the_discounted_amount():
    """Taxing before the discount overcharges, and is the kind of error that
    is only found by a customer's accountant."""
    q = quote(Plan(name="Flat", base_price=Decimal("1000")), Usage(),
              discount_amount=Decimal("100"), tax_rate=Decimal("0.09"))
    assert q.subtotal == Decimal("1000.00")
    assert q.discount_amount == Decimal("100.00")
    assert q.tax_amount == Decimal("81.00")     # 9% of 900
    assert q.total_amount == Decimal("981.00")


def test_a_discount_cannot_exceed_the_bill():
    """A typo in a discount field should not produce an invoice the customer
    could theoretically be paid for."""
    q = quote(Plan(name="Flat", base_price=Decimal("100")), Usage(),
              discount_amount=Decimal("100000"))
    assert q.discount_amount == Decimal("100.00")
    assert q.total_amount == Decimal("0.00")


def test_the_total_is_the_sum_of_the_lines():
    """The invariant the whole design rests on: a total a customer cannot take
    apart is a total they will telephone about."""
    q = quote(STARTER, Usage(cameras=75, sites=9, users=40,
                             storage_gb=Decimal("900")),
              [Module("lpr", "AI LPR", "per_camera", Decimal("5")),
               Module("api", "API Access", "flat", Decimal("100"))])
    assert sum((line.line_total for line in q.lines), Decimal("0")) == q.subtotal


@pytest.mark.parametrize("cameras,expected", [(0, 0), (50, 0), (51, 5), (100, 250)])
def test_the_camera_charge_at_the_boundary(cameras, expected):
    """Off-by-one at the allowance boundary is the error that survives review,
    because fifty-one looks like fifty."""
    q = quote(STARTER, Usage(cameras=cameras))
    line = next((line for line in q.lines if line.kind == "camera"), None)
    assert (line.line_total if line else Decimal(0)) == Decimal(expected)
