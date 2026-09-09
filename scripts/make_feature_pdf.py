r"""Generate the Seventh AI Vision feature reference as a PDF.

Everything here was read out of the codebase — routers, migrations, services
and pages — rather than from a marketing deck, so the counts and capabilities
match what actually ships. It also states known limitations (the CPF rates
that are not implemented, for instance) rather than omitting them, which is
the difference between a reference and a brochure.

Committed alongside docs/Seventh-AI-Vision-Features.pdf so the PDF can be
rebuilt rather than hand-patched as the product moves. The counts near the top
of the story are the ones to refresh; the commands that produce them are in
the block comment below.

Run it in the api image, which already carries reportlab for payslip and
invoice PDFs:

    docker run --rm -v "$PWD:/work" -w /work --entrypoint python         docker-api:latest scripts/make_feature_pdf.py docs/Seventh-AI-Vision-Features.pdf

Refreshing the figures:

    tables     grep -rhoiP 'CREATE TABLE (IF NOT EXISTS )?\K[a-z_]+' backend/alembic/versions/*.py | sort -u | wc -l
    endpoints  grep -rhoP '@router\.(get|post|put|patch|delete)' backend/app/routers/*.py | wc -l
    routers    ls backend/app/routers/*.py | grep -v __init__ | wc -l
    web pages  ls frontend/src/pages/*.tsx | grep -v '\.test\.' | wc -l
    mobile     ls mobile/src/screens/*.tsx | wc -l
    perms      SELECT count(*) FROM permissions;
"""
import sys
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

# Output path, so the same script serves a one-off render and the committed
# document without editing a constant.
OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/Seventh-AI-Vision-Features.pdf"

INK = colors.HexColor("#131722")
INK_MID = colors.HexColor("#414B60")
INK_SOFT = colors.HexColor("#6B7488")
ACCENT = colors.HexColor("#4F46D6")
TEAL = colors.HexColor("#00786B")
RULE = colors.HexColor("#DFE3EB")
BAND = colors.HexColor("#F2F3F9")

styles = getSampleStyleSheet()

S = {
    "title": ParagraphStyle(
        "title", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=30, leading=34, textColor=INK, alignment=TA_LEFT, spaceAfter=6),
    "subtitle": ParagraphStyle(
        "subtitle", parent=styles["Normal"], fontName="Helvetica",
        fontSize=13, leading=18, textColor=INK_MID, alignment=TA_LEFT, spaceAfter=18),
    "eyebrow": ParagraphStyle(
        "eyebrow", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8.5, leading=12, textColor=ACCENT, alignment=TA_LEFT, spaceAfter=10),
    "h1": ParagraphStyle(
        "h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=INK, spaceBefore=16, spaceAfter=2),
    "h2": ParagraphStyle(
        "h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=11, leading=15, textColor=ACCENT, spaceBefore=12, spaceAfter=4),
    "body": ParagraphStyle(
        "body", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, leading=14, textColor=INK_MID, spaceAfter=6),
    "lede": ParagraphStyle(
        "lede", parent=styles["Normal"], fontName="Helvetica-Oblique",
        fontSize=9.5, leading=14, textColor=INK_SOFT, spaceAfter=8),
    "bullet": ParagraphStyle(
        "bullet", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, leading=13.5, textColor=INK_MID,
        leftIndent=11, bulletIndent=2, spaceAfter=2.5),
    "cell": ParagraphStyle(
        "cell", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.5, leading=12, textColor=INK_MID),
    "cellb": ParagraphStyle(
        "cellb", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8.5, leading=12, textColor=INK),
    "num": ParagraphStyle(
        "num", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=17, leading=20, textColor=ACCENT, alignment=TA_CENTER),
    "numlabel": ParagraphStyle(
        "numlabel", parent=styles["Normal"], fontName="Helvetica",
        fontSize=7, leading=9, textColor=INK_SOFT, alignment=TA_CENTER),
    "foot": ParagraphStyle(
        "foot", parent=styles["Normal"], fontName="Helvetica",
        fontSize=7.5, leading=10, textColor=INK_SOFT),
}


def rule(width=170 * mm, color=ACCENT, thickness=2):
    t = Table([[""]], colWidths=[width], rowHeights=[thickness])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color)]))
    return t


def bullets(items):
    return [Paragraph(t, S["bullet"], bulletText="•") for t in items]


def module(number, name, lede, items):
    """One module block, kept on a single page where it fits."""
    block = [
        Paragraph(f"{number}. {name}", S["h1"]),
        rule(width=170 * mm, color=ACCENT, thickness=1.5),
        Spacer(1, 5),
        Paragraph(lede, S["lede"]),
    ]
    block += bullets(items)
    block.append(Spacer(1, 4))
    return KeepTogether(block)


def stat_strip(pairs):
    cells, labels = [], []
    for value, label in pairs:
        cells.append(Paragraph(value, S["num"]))
        labels.append(Paragraph(label, S["numlabel"]))
    w = 170 * mm / len(pairs)
    t = Table([cells, labels], colWidths=[w] * len(pairs))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, colors.white),
    ]))
    return t


def data_table(header, rows, widths):
    data = [[Paragraph(h, S["cellb"]) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c), S["cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor("#EDEFF4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]))
    return t


# ── Page furniture ──────────────────────────────────────────────────────────

def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(INK_SOFT)
    canvas.drawString(20 * mm, 12 * mm, "Seventh AI Vision — Feature Reference")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {canvas.getPageNumber()}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.restoreState()


def on_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(ACCENT)
    canvas.rect(0, 277 * mm, 210 * mm, 20 * mm, stroke=0, fill=1)
    canvas.restoreState()


doc = BaseDocTemplate(
    OUT, pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=20 * mm, bottomMargin=20 * mm,
    title="Seventh AI Vision - Feature Reference",
    author="Seventh AI Vision",
    subject="Complete feature inventory",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
doc.addPageTemplates([
    PageTemplate(id="cover", frames=[frame], onPage=on_cover),
    PageTemplate(id="body", frames=[frame], onPage=on_page),
])

story = []

# ── Cover ───────────────────────────────────────────────────────────────────
story += [
    Spacer(1, 22 * mm),
    Paragraph("ENTERPRISE SECURITY OPERATIONS PLATFORM", S["eyebrow"]),
    Paragraph("Seventh AI Vision", S["title"]),
    Paragraph(
        "The complete feature inventory — AI video surveillance and guard "
        "workforce management in one multi-tenant platform, built for Singapore "
        "security agencies.", S["subtitle"]),
    rule(thickness=3),
    Spacer(1, 10 * mm),
    stat_strip([
        ("149", "DATABASE TABLES"), ("620", "API ENDPOINTS"), ("78", "ROUTERS"),
        ("65", "WEB PAGES"), ("45", "MOBILE SCREENS"), ("11", "AI MODULES"),
    ]),
    Spacer(1, 6),
    stat_strip([
        ("142", "PERMISSIONS"), ("8", "BUILT-IN ROLES"), ("101", "MIGRATIONS"),
        ("3,467", "TESTS"), ("4", "LANGUAGES"), ("3", "CLIENT APPS"),
    ]),
    Spacer(1, 12 * mm),
    Paragraph("What this document is", S["h2"]),
    Paragraph(
        "Every capability listed here was read from the source — the API "
        "routers, the database migrations, the services and the client pages "
        "— not from a specification of what was intended. Where a feature "
        "has a documented limitation, this document says so rather than "
        "omitting it.", S["body"]),
    Paragraph(
        "The platform is one system with two halves that share a tenant, a "
        "permission model and an audit trail: AI-driven video surveillance, and "
        "the workforce management that turns alerts into someone actually "
        "attending. Either half runs without the other — a guarding-only "
        "customer needs no cameras, and a camera-only customer needs no roster "
        "— but the value is in the join.", S["body"]),
    Spacer(1, 8 * mm),
    Paragraph("Generated 9 September 2026", S["foot"]),
    NextPageTemplate("body"),
    PageBreak(),
]

# ── Modules ─────────────────────────────────────────────────────────────────
story.append(Paragraph("Platform capabilities", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))

story.append(module(
    "01", "Multi-tenancy, identity and access",
    "One installation serves many security agencies, isolated in the database "
    "rather than in application code.",
    [
        "<b>Row-level security on every table.</b> Tenant isolation is a Postgres "
        "policy, not a WHERE clause a developer can forget.",
        "<b>Eight built-in roles</b> — Super Admin, Admin, Manager, Supervisor, "
        "Operator, Security Guard, Viewer, Client — plus custom roles defined "
        "per tenant, across 142 permission codes.",
        "<b>Platform-admin protection.</b> A tenant administrator cannot read, "
        "edit, deactivate or reset the credentials of a platform admin sharing "
        "their tenant.",
        "<b>Single sign-on and SCIM</b> for automated user provisioning and "
        "de-provisioning from the customer's own directory.",
        "<b>Two-factor authentication (TOTP)</b>, IP allow-listing, API keys, and "
        "per-user session management with forced revocation and account unlock.",
        "<b>Append-only audit log</b> covering every privileged action.",
        "<b>Per-tenant branding</b> — company name, logo and primary colour — "
        "with the product name held separately so white-labelling never hides "
        "which platform is running.",
        "<b>Per-tenant timezone</b> applied to every date the application renders, "
        "so a Singapore operator never reads a UTC timestamp.",
        "<b>Four languages:</b> English, Chinese, Malay and Tamil.",
    ]))

story.append(module(
    "02", "Video surveillance",
    "Cameras, streams and recordings, with the operator surfaces built around "
    "watching many at once.",
    [
        "<b>Camera management</b> with ONVIF discovery, PTZ control, and RTSP "
        "credential validation before a camera is saved rather than after it fails.",
        "<b>Live Wall</b> — a multi-camera video wall with saved layouts and "
        "profiles, and genuine pop-out windows for multi-screen control rooms.",
        "<b>Continuous recording</b> with retention policies that inherit "
        "tenant to site to camera, so an exception is set once where it applies.",
        "<b>Playback and a recordings library</b>, with export.",
        "<b>Privacy zones</b> masked out of both live view and recordings.",
        "<b>NVR integration</b> and HLS streaming for browser playback.",
        "<b>Site map view</b> plotting cameras, guards and events on a live map.",
        "<b>Detection heat maps</b> and trend analytics per camera and site.",
    ]))

story.append(module(
    "03", "AI analytics",
    "Eleven detection modules, licensed individually per tenant so a customer "
    "pays for what they run.",
    [
        "<b>Licence plate recognition (LPR/ANPR)</b> — entry and exit lanes, "
        "watchlist matching, vehicle journeys.",
        "<b>Face recognition</b> with watchlists and pgvector similarity matching.",
        "<b>Intrusion detection</b> against drawn restricted zones.",
        "<b>PPE compliance</b> — helmet, vest and equipment checks.",
        "<b>Crowd density</b> and zone occupancy.",
        "<b>Fire and smoke detection.</b>",
        "<b>Weapon detection.</b>",
        "<b>Behaviour analysis</b> for anomalous movement.",
        "<b>Camera tampering</b> — obstruction, defocus and repositioning.",
        "<b>Abandoned object detection.</b>",
        "<b>Fall detection</b> for slips and collapses in camera view.",
    ]))

story.append(module(
    "04", "Alerts, incidents and response",
    "Turning a detection into a decision, and a decision into an attended "
    "response.",
    [
        "<b>Alert rules engine</b> with per-module thresholds, schedules and "
        "site scoping.",
        "<b>Deduplication rules</b> so one event does not become forty alerts.",
        "<b>Alert routing</b> to the right people by role, site and severity.",
        "<b>Incident management</b> — assignment, notes, severity, status and "
        "resolution, with bulk actions.",
        "<b>SLA configuration and escalation</b> when an incident is not "
        "acknowledged in time.",
        "<b>Command Centre</b> — the live operational picture across every site.",
        "<b>Action Centre</b> — cross-role duty guidance telling each person "
        "what needs them next.",
        "<b>Emergency broadcast</b> to all staff, a role or a site, with "
        "per-recipient acknowledgement tracking.",
        "<b>Dispatch</b> for assigning and tracking response.",
        "<b>Notification channels and rules</b> — in-app, push and webhook, "
        "with a delivery log.",
    ]))

story.append(module(
    "05", "Roster and scheduling",
    "Building the roster, and keeping it true when reality interferes.",
    [
        "<b>Shift patterns</b> per site, guard and weekday, expanded into "
        "concrete shifts on a rolling window.",
        "<b>AI auto-scheduler</b> scoring candidates against nine rules: minimum "
        "rest (11 hours), maximum consecutive days (6), day/night balance, stated "
        "preference, approved leave, the site's own duty team, minimum coverage, "
        "supervisor presence, and off-day stagger.",
        "<b>Per-shift-type duty strength.</b> A site is staffed separately for "
        "day and night duty, and the scheduler fills every post — not one "
        "guard and a warning.",
        "<b>Duty Teams</b> — named day and night teams per site, with the "
        "shortest-staffed sites sorted first. The scheduler draws from the team, "
        "falling back to the wider pool rather than leaving a post empty, and "
        "flags when it had to.",
        "<b>Guard shift preferences</b> — preferred shift type and off days, "
        "set on the guard's own record and honoured by the scheduler.",
        "<b>Draft rosters</b> reviewed and edited before publishing, with warnings "
        "for unfilled posts, coverage shortfalls and missing supervisors.",
        "<b>Schedules a whole calendar month in one action</b>, or any range up to "
        "62 days, with the rules configured per run rather than read out of the "
        "source.",
        "<b>Cover requests.</b> When approved leave lands on a published shift, "
        "the uncovered post joins a queue that stays open until a supervisor "
        "assigns a replacement or records that none is needed. The system refuses "
        "a replacement who is on leave or already rostered on an overlapping shift.",
    ]))

story.append(module(
    "06", "Attendance",
    "Proving a guard was where they were rostered, when they were rostered.",
    [
        "<b>Selfie check-in and check-out</b> with liveness scoring to reject a "
        "photograph of a photograph.",
        "<b>Anti-spoof location</b> — mock-GPS detection flagged on the record.",
        "<b>Site geofence</b> as a radius or a drawn polygon, with off-site "
        "check-ins marked rather than silently accepted.",
        "<b>Per-site late grace</b> — a remote gate with one bus an hour cannot "
        "hold the same standard as a lobby on a train line.",
        "<b>Live attendance board</b> with eight states: on time, late, on break, "
        "due now, not reported, upcoming shift, approved leave and checked out.",
        "<b>Escalation ordering</b> — sites with guards due and nobody reported "
        "sort to the top under a no-guard-on-site banner.",
        "<b>Correction queue</b> for supervisor-approved amendments.",
    ]))

story.append(module(
    "07", "Guard operations",
    "What a guard actually does on shift, and the record it leaves.",
    [
        "<b>Guard tours</b> — routes and checkpoints scanned by QR or NFC, with "
        "tour schedules and compliance reporting.",
        "<b>Digital Occurrence Book</b> — append-only with immutable timestamps, "
        "built against the Private Security Industry Act requirement. Thirteen "
        "entry types.",
        "<b>SOP / post orders</b> per site, with per-guard acknowledgement so "
        "there is a record of who read what.",
        "<b>Violations</b> raised against a guard, with review.",
        "<b>GPS tracking</b> and geofence entry/exit events.",
        "<b>SOS panic button</b> — writes a critical occurrence entry, raises an "
        "incident, and pushes a real-time alert to every supervisor watching.",
        "<b>Body-worn cameras</b> — assignment, recordings and events.",
        "<b>Man down.</b> The guard's phone watches its accelerometer and raises "
        "an alert when they stop moving, or are knocked and then lie still. A "
        "countdown they can cancel avoids a phone left on a desk waking the "
        "control room — but the deadline is held on the server, so a handset that "
        "shattered on impact, ran flat or lost signal still gets help sent. Off by "
        "default; the thresholds are a tenant setting.",
    ]))

story.append(module(
    "08", "The guardhouse registers",
    "The books kept on paper at almost every site — the ones a client audit "
    "asks to see first, and the ones nobody can report on.",
    [
        "<b>Key register.</b> A cabinet per site, and a log of who took which key, "
        "when, and whether it came back. Built so the useful question — what is "
        "still out right now — is answered directly, and the database refuses to "
        "let one key be issued twice. Keys go to contractors and tenants as often "
        "as to staff, so a holder can be a named person without an account.",
        "<b>Lost and found.</b> What was handed in, where it is stored, and who "
        "took it away. Only the last few characters of a claimant's identity "
        "document are ever stored — the check that matters is against the card in "
        "their hand. Property held past the retention period is reported, because "
        "an item kept indefinitely is a PDPA problem accruing quietly.",
        "<b>Facility defect log.</b> The blown stairwell light, the leak in the car "
        "park. The status ladder stops where a security company's control does: "
        "the record carries who it was referred to and the building's own ticket "
        "number, so chasing it is possible. Safety hazards and anything open past "
        "a fortnight are surfaced separately.",
        "<b>Equipment register.</b> Radios, torches, batons and body cameras "
        "signed out to a named officer. The condition it comes back in writes "
        "through to the item, so the next officer is not issued a broken torch.",
        "<b>Uniform register.</b> Issued by quantity and size, with partial "
        "returns — a guard who resigns hands back three of four shirts — and the "
        "deposit held against the kit.",
        "<b>One question answers both:</b> what is this officer holding? The "
        "screen a supervisor opens on somebody's last day.",
        "<b>Structured shift handover.</b> Two signatures, not one person's note. "
        "The outgoing guard completes a checklist configured per site; the "
        "incoming guard accepts it or DISPUTES it. A counted item is measured "
        "against the register, so eleven keys against a handover claiming twelve "
        "is visible rather than signed for. Every handover carries the keys still "
        "out, property held, kit signed out and defects open.",
    ]))

story.append(module(
    "09", "Visitor management (VMS)",
    "The gatehouse: every visitor, not just the ones in vehicles.",
    [
        "<b>All visit types</b> — walk-in, delivery, drop-off and pick-up — "
        "on one board.",
        "<b>Per-site form configuration.</b> Each site defines its own fields, "
        "types and required flags.",
        "<b>Pre-registration</b> as a dialog on the same page, so the gatehouse "
        "never navigates away mid-queue.",
        "<b>Fast LPR entry</b> — the plate is read and the record opened with "
        "known details already filled, because a vehicle at the barrier blocks "
        "the road while anyone types.",
        "<b>Visitor labels with QR</b>, 62 mm, printed at the operator's "
        "discretion rather than automatically.",
        "<b>QR check-out</b> — scanning the label closes that visit.",
        "<b>Exit-camera auto check-out</b> from an LPR read.",
        "<b>Automatic day-close</b> so a visit never runs past its own day.",
        "<b>Full action log</b> naming the operator behind every entry and exit.",
    ]))

story.append(module(
    "10", "Physical security and IoT",
    "The hardware around the guard.",
    [
        "<b>Access control</b> — doors, credentials, rules and an event log.",
        "<b>Barrier control</b> with commanded open/close and an audit of who "
        "commanded it.",
        "<b>Alarm panels and zones</b> with event history.",
        "<b>IoT sensors</b> — readings, thresholds and alerts.",
        "<b>Car park management</b> — car parks, bays, zones, tariffs, sessions "
        "and dedicated LPR cameras, with a free-parking allowance per site.",
        "<b>Device protocol support</b> for third-party integration.",
    ]))

story.append(module(
    "11", "HR, leave, training and payroll",
    "The workforce behind the roster.",
    [
        "<b>Employee records</b> — NRIC/FIN, date of birth, nationality, work "
        "pass type and expiry, address, emergency contact, bank details and "
        "designation.",
        "<b>Document management</b> with expiry tracking and expiring-soon "
        "warnings on passports, work passes and certifications.",
        "<b>Leave</b> — configurable types, entitlement balances, requests with "
        "supporting documents, and approval that feeds the roster directly.",
        "<b>Training</b> — courses, a real quiz engine with question banks, "
        "attempts and pass records.",
        "<b>Certifications</b> with issuing body, number and expiry.",
        "<b>Payroll</b> — CPF by age band with the S$7,400 Ordinary Wage ceiling, "
        "work-pass eligibility, overtime at 1.5x, and monthly, daily or hourly "
        "pay bases.",
        "<b>Timesheet approval.</b> A human between a check-in scan and a payslip. "
        "Hours are snapshotted when submitted, so an approval stays a signature on "
        "a fixed document rather than on one that keeps changing underneath. "
        "Payroll always reports which guards have no approved timesheet, and can "
        "be set to refuse to pay them.",
        "<b>Public holidays and shift allowances.</b> A gazetted holiday calendar "
        "per tenant, holiday pay, and named allowances attached to a shift type.",
        "<b>Overtime cap.</b> Hours beyond the 72-hour monthly statutory limit are "
        "flagged on the run rather than paid silently.",
        "<b>PWM grade</b> on the officer record, across the seven-grade ladder.",
        "<b>PLRD licence number and expiry</b> tracked per officer.",
        "<b>Payslip PDFs</b> and an <b>IR8A</b> annual summary and PDF.",
        "<i>Documented limitations:</i> graduated first- and second-year PR CPF "
        "rates and the Additional Wage ceiling are not implemented.",
    ]))

story.append(module(
    "12", "Commercial",
    "Getting paid for the hours actually worked.",
    [
        "<b>Client management</b> with contacts and billing addresses.",
        "<b>Invoices generated from real attendance</b> — the loop closes from "
        "roster to check-in to worked hours to invoice line, per site.",
        "<b>Per-site bill rates</b> with overtime, tax and invoice PDFs.",
        "<b>Client portal</b> — the customer signs in and sees their own sites, "
        "incidents and invoices, and nothing else.",
        "<b>SaaS billing</b> — plans, subscriptions, products and per-module "
        "licensing for the platform operator.",
    ]))

story.append(module(
    "13", "Compliance, evidence and reporting",
    "The record that survives an audit.",
    [
        "<b>Evidence management</b> with a chain-of-custody access log recording "
        "every view and download.",
        "<b>PDPA</b> — consent records and data subject access requests.",
        "<b>Contractor accreditation</b> and work permit tracking.",
        "<b>Data retention policies</b> per tenant, site and camera.",
        "<b>Scheduled reports</b> delivered on a recurring basis.",
        "<b>Exports</b> in operational formats, and a full analytics suite.",
    ]))

story.append(PageBreak())

# ── Clients ─────────────────────────────────────────────────────────────────
story.append(Paragraph("Client applications", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))
story.append(data_table(
    ["Application", "Platform", "Scale", "Notes"],
    [
        ["Web console", "Browser (React)", "58 pages",
         "The full platform. Every module, every administrative surface."],
        ["Mobile app", "iOS and Android (React Native)", "40 screens",
         "Built for the guard on shift: check-in, patrol scanning, occurrence "
         "book, SOP, incidents, SOS. Offline outbox queues actions taken with no "
         "signal and sends them when it returns."],
        ["Windows desktop", "Electron", "Same as web",
         "Packaged installer for control-room machines, with auto-update."],
    ],
    widths=[32 * mm, 38 * mm, 20 * mm, 80 * mm]))

story.append(Spacer(1, 10))
story.append(Paragraph("Roles", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "Eight built-in roles ship with the platform; tenants may define their own "
    "in addition. A role cannot grant a permission it does not itself hold.",
    S["body"]))
story.append(data_table(
    ["Role", "Typical holder", "Scope"],
    [
        ["Super Admin", "Platform operator", "Cross-tenant administration"],
        ["Admin", "Agency owner or operations director", "Everything within one tenant"],
        ["Manager", "Operations manager", "Multi-site oversight and approvals"],
        ["Supervisor", "Site or shift supervisor", "Rostering, attendance and incident response"],
        ["Operator", "Control room operator", "Live monitoring, alerts and dispatch"],
        ["Security Guard", "Officer on shift", "Mobile app: own shifts, patrols and reports"],
        ["Viewer", "Auditor or observer", "Read-only"],
        ["Client", "The agency's customer", "Their own sites, incidents and invoices"],
    ],
    widths=[30 * mm, 62 * mm, 78 * mm]))

story.append(Spacer(1, 10))
story.append(Paragraph("Built for Singapore", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))
story += bullets([
    "<b>Digital Occurrence Book</b> built against the Private Security Industry "
    "Act requirement for a licensed agency to maintain one — append-only, "
    "with no edit or delete path.",
    "<b>CPF</b> calculated by age band against the S$7,400 Ordinary Wage ceiling, "
    "with eligibility determined by work pass type.",
    "<b>IR8A</b> annual reporting.",
    "<b>PWM grades</b> across the seven-grade Progressive Wage Model ladder, and "
    "<b>PLRD licence</b> number and expiry per officer.",
    "<b>Gazetted public holidays</b> with holiday pay, and the 72-hour monthly "
    "overtime cap flagged on every payroll run.",
    "<b>SGD invoicing</b> with purchase-order and payment-terms fields.",
    "<b>Work pass tracking</b> with expiry warnings.",
    "<b>PDPA</b> consent and data subject request handling.",
    "<b>Asia/Singapore</b> as the tenant timezone, applied to every rendered date.",
    "<b>Four languages</b> reflecting the local workforce: English, Chinese, "
    "Malay and Tamil.",
])

story.append(Spacer(1, 14))
story.append(rule(thickness=0.7, color=RULE))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "Compiled from the Seventh AI Vision source repository — 101 database "
    "migrations, 78 API routers, 620 endpoints, 32 backend services and 110 "
    "client screens, covered by 3,467 automated tests. Capability counts reflect "
    "what is implemented and running, not what is planned.", S["foot"]))

doc.build(story)
print("WROTE", OUT)
