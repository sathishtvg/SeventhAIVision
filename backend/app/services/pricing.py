"""What a customer owes for a period, and a line for every part of it.

§13 asks for a pricing engine that combines a fixed subscription with per
camera, per site, per user, per module and per gigabyte charges. The shape it
produces is:

    base plan
  + per-unit charges for whatever exceeds the plan's allowance
  + module charges, each priced by how that module is sold
  - discount
  + tax
  = total

EVERY CHARGE BECOMES A LINE. A total a customer cannot take apart is a total
they will telephone about, and "why is it $4,200 this month" should be
answerable by reading the invoice rather than by someone opening a database.
So the engine returns lines, and the total is their sum — never the other way
round.

ALLOWANCES ARE SUBTRACTED BEFORE ANYTHING IS CHARGED PER UNIT. A plan including
fifty cameras, used for sixty, bills ten. Charging all sixty is the oldest
billing complaint there is.

DECIMAL, NOT FLOAT, THROUGHOUT. 0.1 + 0.2 is not 0.3 in binary floating point,
and an invoice a cent out is a customer who stops trusting the whole document.
Rounding happens once per line, to the cent, half-up — the way an accountant
does it, not the way Python's banker's rounding does.

NOTHING HERE WRITES. It is given usage and a plan and returns what that costs,
so it can be run to preview a bill, to check one, or to produce one, and tested
without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def money(value) -> Decimal:
    """To the cent, half-up.

    Python rounds half to even by default — round(0.5) is 0 — which is correct
    for statistics and wrong for money. An invoice that rounds a customer's
    charge down half the time is a discrepancy somebody eventually reconciles.
    """
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass
class Usage:
    """What the customer actually ran during the period."""
    cameras: int = 0
    sites: int = 0
    users: int = 0
    storage_gb: Decimal = Decimal("0")


@dataclass
class Plan:
    """The commercial shape of a plan, as billing_plans stores it."""
    name: str = "Custom"
    base_price: Decimal = Decimal("0")
    included_cameras: int = 0
    included_sites: int = 0
    included_users: int = 0
    included_storage_gb: int = 0
    price_per_camera: Decimal = Decimal("0")
    price_per_site: Decimal = Decimal("0")
    price_per_user: Decimal = Decimal("0")
    price_per_gb: Decimal = Decimal("0")


@dataclass
class Module:
    """A module the customer has switched on, and how it is sold."""
    code: str
    name: str
    billing_type: str          # included | flat | per_camera | per_site | per_user
    unit_price: Decimal = Decimal("0")


@dataclass
class Line:
    kind: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    module_code: str | None = None


@dataclass
class Quote:
    lines: list[Line] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")


def _overage(used: int, included: int) -> int:
    """What is charged per unit: usage above the allowance, never below zero.

    A customer under their allowance is not owed money, which sounds obvious
    until a negative quantity reaches an invoice and reduces the bill.
    """
    return max(0, used - included)


def _module_quantity(module: Module, usage: Usage) -> Decimal:
    if module.billing_type == "per_camera":
        return Decimal(usage.cameras)
    if module.billing_type == "per_site":
        return Decimal(usage.sites)
    if module.billing_type == "per_user":
        return Decimal(usage.users)
    if module.billing_type == "flat":
        return Decimal(1)
    return Decimal(0)  # included


def quote(
    plan: Plan,
    usage: Usage,
    modules: list[Module] | None = None,
    *,
    discount_amount: Decimal | float | str = 0,
    tax_rate: Decimal | float | str = 0,
) -> Quote:
    """Price one period. Pure: same inputs, same answer, no database."""
    lines: list[Line] = []

    if plan.base_price and money(plan.base_price) > 0:
        lines.append(Line(
            kind="base",
            description=f"{plan.name} subscription",
            quantity=Decimal(1),
            unit_price=money(plan.base_price),
            line_total=money(plan.base_price),
        ))

    # ── Per-unit charges, above the allowance ────────────────────────────────
    for kind, used, included, rate, noun in (
        ("camera", usage.cameras, plan.included_cameras, plan.price_per_camera, "camera"),
        ("site", usage.sites, plan.included_sites, plan.price_per_site, "site"),
        ("user", usage.users, plan.included_users, plan.price_per_user, "user"),
    ):
        over = _overage(used, included)
        if over and money(rate) > 0:
            lines.append(Line(
                kind=kind,
                description=(f"{over} {noun}{'s' if over != 1 else ''} above the "
                             f"{included} included"),
                quantity=Decimal(over),
                unit_price=money(rate),
                line_total=money(Decimal(over) * Decimal(str(rate))),
            ))

    storage_over = max(Decimal("0"),
                       Decimal(str(usage.storage_gb)) - Decimal(plan.included_storage_gb))
    if storage_over > 0 and Decimal(str(plan.price_per_gb)) > 0:
        lines.append(Line(
            kind="storage",
            description=(f"{storage_over} GB above the "
                         f"{plan.included_storage_gb} GB included"),
            quantity=storage_over,
            unit_price=Decimal(str(plan.price_per_gb)),
            line_total=money(storage_over * Decimal(str(plan.price_per_gb))),
        ))

    # ── Modules ──────────────────────────────────────────────────────────────
    #
    # An included module still gets a line, at zero. A customer looking at what
    # they pay for should see everything they have, and a module that silently
    # vanishes from the bill looks like one they are not entitled to.
    for module in modules or []:
        quantity = _module_quantity(module, usage)
        unit = money(module.unit_price)
        if module.billing_type == "included" or unit == 0:
            lines.append(Line(
                kind="module", module_code=module.code,
                description=f"{module.name} (included)",
                quantity=Decimal(1), unit_price=Decimal("0.00"),
                line_total=Decimal("0.00"),
            ))
            continue
        if quantity <= 0:
            continue
        lines.append(Line(
            kind="module", module_code=module.code,
            description=f"{module.name} × {quantity}",
            quantity=quantity, unit_price=unit,
            line_total=money(quantity * unit),
        ))

    subtotal = money(sum((line.line_total for line in lines), Decimal("0")))
    discount = money(discount_amount)
    # A discount larger than the bill is a data-entry mistake, and letting it
    # through produces a negative invoice nobody can pay.
    discount = min(discount, subtotal)
    taxable = subtotal - discount
    rate = Decimal(str(tax_rate))
    tax = money(taxable * rate)

    return Quote(
        lines=lines,
        subtotal=subtotal,
        discount_amount=discount,
        tax_rate=rate,
        tax_amount=tax,
        total_amount=money(taxable + tax),
    )
