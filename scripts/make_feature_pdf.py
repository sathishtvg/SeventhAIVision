r"""Generate the Seventh AI Vision feature reference as a PDF.

Everything here was read out of the codebase — routers, migrations, services
and pages — rather than from a marketing deck, so the counts and capabilities
match what actually ships. It also states known limitations (the CPF rates
that are not implemented, the drone that has only flown in a simulator) rather
than omitting them, which is the difference between a reference and a brochure.

Committed alongside docs/Seventh-AI-Vision-Features.pdf so the PDF can be
rebuilt rather than hand-patched as the product moves. THIS IS A LIVING
DOCUMENT: when a feature lands, move its row out of PLANNED below into a module
block, refresh the figures, add a line to EDITIONS, and rebuild.

Run it in the api image, which already carries reportlab for payslip and
invoice PDFs:

    docker run --rm -v "$PWD:/work" -w /work --entrypoint python         docker-api:latest scripts/make_feature_pdf.py docs/Seventh-AI-Vision-Features.pdf

Refreshing the figures (count what is merged to main, not what is in progress):

    tables      SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'
                   AND table_type = 'BASE TABLE' AND table_name NOT LIKE '%\_p20%' AND table_name NOT LIKE '%\_default';
    operations  the paths and methods of get_openapi(routes=app.routes)   (the served /openapi.json is disabled)
    modules     ls backend/app/routers/*.py | grep -v __init__ | wc -l
    web routes  grep -c '<Route ' frontend/src/App.tsx
    mobile      ls mobile/src/screens/*.tsx | grep -v '\.test\.' | wc -l
    perms       SELECT count(*) FROM permissions;
    tests       the totals of the four suites: backend, repository inspection, web, phone
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

# ── What this edition is ────────────────────────────────────────────────────
GENERATED = "8 October 2026"
EDITION = "Edition 4"
HEAD = "0151"
#: Every edition, oldest first. Add a row; never rewrite one.
EDITIONS = [
    ["Edition 1", "9 September 2026", "0100",
     "The first reference: thirteen modules, from identity to compliance."],
    ["Edition 2", "7 October 2026", "0148",
     "Adds the vendor's platform console, Virtual Patrolling, Drone Patrol, AI Security "
     "Intelligence, and the first six phases of the enterprise expansion: investigation, "
     "evidence packages, the security map, guard response with SLA and escalation, the "
     "occurrence book reviewed and summarised, and the SOP library. Adds the sections on "
     "where AI is used, what is in progress, and known limits."],
    ["Edition 3", "8 October 2026", "0150",
     "Adds phases 7 and 8 of the enterprise expansion: visitor and contractor "
     "authorisation (module 23), and device health, the asset register and "
     "maintenance work orders (module 24). The vendor's console becomes module 25."],
    ["Edition 4", "8 October 2026", "0151",
     "Adds phase 9 of the enterprise expansion: risk patterns and advice (module 25) — "
     "where what went wrong gathers, what stands out with how much history it rests on, "
     "and a person's answer. The vendor's console becomes module 26."],
]

INK = colors.HexColor("#131722")
INK_MID = colors.HexColor("#414B60")
INK_SOFT = colors.HexColor("#6B7488")
ACCENT = colors.HexColor("#4F46D6")
TEAL = colors.HexColor("#00786B")
AMBER = colors.HexColor("#9A5B00")
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
    "part": ParagraphStyle(
        "part", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, textColor=INK, spaceBefore=4, spaceAfter=4),
    "h1": ParagraphStyle(
        "h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=INK, spaceBefore=16, spaceAfter=2),
    "h2": ParagraphStyle(
        "h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=11, leading=15, textColor=ACCENT, spaceBefore=12, spaceAfter=4),
    "tag": ParagraphStyle(
        "tag", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=7, leading=9, textColor=TEAL, spaceBefore=14, spaceAfter=0),
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


def module(number, name, lede, items, tag=None):
    """One module block. Its heading, rule, lede and first point stay together;
    the rest may run on to the next page, so a long module does not leave half
    a page empty before it."""
    h1 = S["h1"] if tag is None else ParagraphStyle("h1tag", parent=S["h1"], spaceBefore=2)
    head = ([Paragraph(tag, S["tag"])] if tag else []) + [
        Paragraph(f"{number}. {name}", h1),
        rule(width=170 * mm, color=ACCENT, thickness=1.5),
        Spacer(1, 5),
        Paragraph(lede, S["lede"]),
    ]
    points = bullets(items)
    return [KeepTogether(head + points[:2])] + points[2:] + [Spacer(1, 4)]


def part(title, lede):
    return [
        Paragraph(title, S["part"]),
        rule(thickness=2),
        Spacer(1, 6),
        Paragraph(lede, S["body"]),
    ]


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
    canvas.drawString(20 * mm, 12 * mm, f"Seventh AI Vision — Feature Reference · {EDITION}, {GENERATED}")
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
    subject=f"Complete feature inventory - {EDITION}, {GENERATED}",
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
        "The complete feature inventory — AI video surveillance, security "
        "intelligence and guard workforce management in one multi-tenant "
        "platform, built for Singapore security agencies.", S["subtitle"]),
    rule(thickness=3),
    Spacer(1, 10 * mm),
    stat_strip([
        ("274", "DATABASE TABLES"), ("997", "API OPERATIONS"), ("102", "API MODULES"),
        ("106", "WEB ROUTES"), ("52", "MOBILE SCREENS"), ("11", "AI DETECTION MODULES"),
    ]),
    Spacer(1, 6),
    stat_strip([
        ("197", "PERMISSIONS"), ("8", "BUILT-IN ROLES"), ("151", "MIGRATIONS"),
        ("5,920", "AUTOMATED TESTS"), ("4", "LANGUAGES"), ("3", "CLIENT APPS"),
    ]),
    Spacer(1, 10 * mm),
    Paragraph("What this document is", S["h2"]),
    Paragraph(
        "Every capability listed here was read from the source — the API "
        "routers, the database migrations, the services and the client pages "
        "— not from a specification of what was intended. Where a feature "
        "has a documented limitation, this document says so rather than "
        "omitting it. What is still being built is listed separately, under "
        "<i>In progress and planned</i>, and is not counted in the figures above.", S["body"]),
    Paragraph(
        "The platform is one system whose parts share a tenant, a permission "
        "model and an audit trail: AI-driven video surveillance, the security "
        "intelligence that relates what the cameras, drones, patrols and guards "
        "report, and the workforce management that turns an alert into someone "
        "actually attending. Each part runs without the others — a "
        "guarding-only customer needs no cameras, and a camera-only customer "
        "needs no roster — but the value is in the join.", S["body"]),
    Paragraph(
        "<b>One rule runs through all of it:</b> AI detects, relates, assesses "
        "and recommends. An authorised person makes the security decision.", S["body"]),
    Spacer(1, 6 * mm),
    Paragraph(f"{EDITION} · generated {GENERATED} · platform at migration {HEAD}", S["foot"]),
    NextPageTemplate("body"),
    PageBreak(),
]

# ── About this edition ──────────────────────────────────────────────────────
story += [
    Paragraph("About this edition", S["h1"]),
    rule(thickness=2),
    Spacer(1, 8),
    Paragraph(
        "This is a living document. It is rebuilt from a script kept beside it "
        "in the repository (<font face='Courier'>scripts/make_feature_pdf.py</font>) "
        "each time the platform moves, so it is regenerated rather than "
        "hand-edited. A feature appears in the numbered modules only once it is "
        "merged, tested and running; until then it is in the table of what is in "
        "progress and planned.", S["body"]),
]
story.append(data_table(
    ["Edition", "Date", "Platform at", "What it covers"],
    EDITIONS, widths=[22 * mm, 30 * mm, 22 * mm, 96 * mm]))
story += [
    Spacer(1, 8),
    Paragraph("How the document is laid out", S["h2"]),
]
story += bullets([
    "<b>At a glance</b> — one page for the person who signs.",
    "<b>Part 1, modules 01 to 13</b> — the platform as the first edition described "
    "it, brought up to date.",
    "<b>Part 2, modules 14 to 26</b> — what has been added since: patrols from a "
    "screen and from the air, security intelligence, and the enterprise expansion.",
    "<b>Where AI is used, and where a person decides</b> — every place a model or a "
    "rule does work, what it is trusted with, and what stays a person's. "
    "<b>AI capabilities added or upgraded later are recorded here.</b>",
    "<b>In progress and planned</b> — what is being built now and what follows.",
    "<b>Client applications, roles, Singapore, and known limits.</b>",
])
story.append(PageBreak())

# ── At a glance ─────────────────────────────────────────────────────────────
#
# One page for the person who signs, ahead of the inventory written for the
# person who evaluates. Outcomes rather than features: the modules are listed
# one line each so the breadth is visible without reading to page ten.
story += [
    Paragraph("At a glance", S["h1"]),
    rule(thickness=2),
    Spacer(1, 8),
    Paragraph(
        "A security agency runs on four or five systems that do not talk to "
        "each other: a spreadsheet roster, a messaging group for incidents, a "
        "paper occurrence book at every guardhouse, a payroll spreadsheet, and a "
        "filing cabinet for whatever a client asks about later. This is one "
        "system that replaces all of them.", S["body"]),
    Spacer(1, 6),

    Paragraph("What changes", S["h2"]),
]
story.append(data_table(
    ["", "Today", "With Seventh AI Vision"],
    [
        ["Client asks for proof of a patrol",
         "Phone calls, a photo of a page",
         "On screen in seconds — or the client checks their own login"],
        ["A key is unaccounted for",
         "Nobody knows until it is needed",
         "Shown at every handover, with who took it and when"],
        ["Leave approved on a live roster",
         "Someone remembers, or nobody does",
         "The uncovered post queues until it is filled"],
        ["Month-end payroll",
         "Hours retyped from timesheets",
         "From real check-ins, supervisor-approved first"],
        ["A lone officer collapses",
         "Nothing, until somebody notices",
         "Man-down alerts the control room with their last position"],
        ["Invoicing a client",
         "Estimated from the contract",
         "From the hours actually worked at their site"],
        ["Five alerts from one event",
         "Five things to read, on five screens",
         "One situation, with what is unusual about it and a suggested response"],
        ["\"Where was that vehicle seen?\"",
         "Somebody scrubs through recordings",
         "Every sighting of the plate, in order, with time and distance between"],
        ["Evidence asked for months later",
         "Whatever has not been purged",
         "A sealed package under a hold, with its chain of custody"],
        ["A guard is sent to an incident",
         "A call, and hoping",
         "Accepted, on the way, arrived, reported — each recorded, and late ones escalated"],
    ],
    widths=[46 * mm, 48 * mm, 76 * mm]))
story += [
    Spacer(1, 8),
    Paragraph("The platform in twenty-five lines", S["h2"]),
]
story += bullets([
    "<b>Identity and access</b> — 8 roles, 197 permissions, SSO, 2FA, audit log, "
    "per-tenant branding, four languages.",
    "<b>Video surveillance</b> — cameras, live wall, recording with checksums, "
    "playback, privacy zones, NVR.",
    "<b>AI analytics</b> — eleven detection modules, licensed individually.",
    "<b>Alerts and incidents</b> — triage, dispatch, escalation, emergency broadcast.",
    "<b>Roster</b> — a whole month auto-scheduled against nine rules, reviewed "
    "as a draft before it goes live.",
    "<b>Attendance</b> — face check-in with liveness, geofenced, eight live states.",
    "<b>Guard operations</b> — patrol scanning, occurrence book, post orders, "
    "panic button, man down.",
    "<b>Guardhouse registers</b> — keys, lost &amp; found, defects, equipment, "
    "uniform, and a handover two people sign.",
    "<b>Visitor management</b> — pre-registration, passes, contractors, deliveries.",
    "<b>Physical security and IoT</b> — access control, alarms, barriers, parking.",
    "<b>HR and payroll</b> — leave, training, timesheets, CPF, overtime, public "
    "holidays, PWM grades and wage floors, IR8A.",
    "<b>Commercial</b> — invoices from real hours, and a client portal.",
    "<b>Compliance</b> — chain of custody, PDPA, retention, scheduled reports.",
    "<b>Virtual Patrolling</b> — a scheduled camera-by-camera inspection from a "
    "screen, with a report.",
    "<b>Drone Patrol</b> — planned flights whose camera the AI watches like any other.",
    "<b>AI Security Intelligence</b> — events related into situations, assessed, "
    "with a suggestion a person decides on.",
    "<b>Smart Investigation</b> — one search across fourteen sources, and the "
    "trail of a plate or a watchlist face.",
    "<b>Evidence packages</b> — sealed, checksummed, held against deletion, exported.",
    "<b>Security map</b> — cameras, guards, drones, incidents and the places of a "
    "site on one map.",
    "<b>Guard response</b> — the steps of a response, three SLA clocks, "
    "escalation policies.",
    "<b>Occurrence book, reviewed</b> — search, supervisor review, correction, "
    "standing instructions, a shift summary.",
    "<b>SOP library</b> — versioned, approved procedures, quoted beside the incident.",
    "<b>Visitor authorisation</b> — a host's yes or no to a visit, for where and "
    "for how long, read at the gate.",
    "<b>Assets and maintenance</b> — device health as devices report it, an asset "
    "register, and work orders a person accepts.",
    "<b>Platform console</b> — the vendor's own: customers, pricing, invoicing, "
    "errors, support sessions.",
])
story += [
    Spacer(1, 8),
    Paragraph("Built for Singapore, not adapted to it", S["h2"]),
    Paragraph(
        "PLRD licence expiry per officer, and what a site requires an officer to "
        "hold. PWM grades across the seven-grade ladder, with wage floors pay is "
        "checked against. CPF by age band against the Ordinary Wage ceiling. "
        "Gazetted public holidays and holiday pay. The 72-hour monthly overtime "
        "cap flagged on every run. IR8A. PDPA consent and data subject requests. "
        "An occurrence book built against the Private Security Industry Act. SGD "
        "invoicing. English, Chinese, Malay and Tamil. Runs hosted or on your own "
        "servers.", S["body"]),
    PageBreak(),
]

# ═══ PART 1 ═════════════════════════════════════════════════════════════════
story += part(
    "Part 1 — Platform capabilities",
    "The thirteen modules of the first edition, brought up to date. Where a "
    "module has grown into one of its own since, it says where to read on.")

story += module(
    "01", "Multi-tenancy, identity and access",
    "One installation serves many security agencies, isolated in the database "
    "rather than in application code.",
    [
        "<b>Row-level security on every table.</b> Tenant isolation is a Postgres "
        "policy, forced even on the table owner, not a WHERE clause a developer can "
        "forget. Records that must not be rewritten are granted insert and read "
        "only, and the application's database role cannot empty a table.",
        "<b>Eight built-in roles</b> — Super Admin, Admin, Manager, Supervisor, "
        "Operator, Security Guard, Viewer, Client — plus custom roles defined "
        "per tenant, across 197 permission codes.",
        "<b>Site scope.</b> A user can be held to particular sites; every list, "
        "search and record honours it.",
        "<b>The platform operator is not a customer's administrator.</b> Super "
        "Admin runs the platform from a tenant of its own and holds none of a "
        "customer's day-to-day permissions (module 26).",
        "<b>Single sign-on and SCIM</b> for automated user provisioning and "
        "de-provisioning from the customer's own directory.",
        "<b>Two-factor authentication (TOTP)</b> — required, not optional, for "
        "the platform owner — with IP allow-listing, API keys, rate limits, and "
        "per-user session management with forced revocation and account unlock.",
        "<b>Hash-chained audit log</b> covering every privileged action, with the "
        "actor, role, tenant, site, result and request id, and verifiable end to end.",
        "<b>Per-tenant branding</b> — company name, logo and primary colour — "
        "with the product name held separately so white-labelling never hides "
        "which platform is running.",
        "<b>Per-tenant timezone</b> applied to every date the application renders, "
        "so a Singapore operator never reads a UTC timestamp.",
        "<b>Four languages:</b> English, Chinese, Malay and Tamil.",
    ])

story += module(
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
        "<b>Recording integrity.</b> Each recording carries a checksum that is "
        "verified, and the platform's health check reports how many streams should "
        "be recording against how many are.",
        "<b>Playback and a recordings library</b>, with export.",
        "<b>Privacy zones</b> masked out of both live view and recordings.",
        "<b>NVR integration</b> and HLS streaming for browser playback.",
        "<b>Site map view</b> plotting sites with live camera health and manning "
        "(the full security map is module 19).",
        "<b>Detection heat maps</b> and trend analytics per camera and site.",
    ])

story += module(
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
        "The same detectors watch a drone's camera (module 15), and what they "
        "report is related and assessed by the intelligence layer (module 16).",
    ])

story += module(
    "04", "Alerts, incidents and response",
    "Turning a detection into a decision, and a decision into an attended "
    "response.",
    [
        "<b>Alert rules engine</b> with per-module thresholds, schedules and "
        "site scoping.",
        "<b>Deduplication rules</b> so one event does not become forty alerts.",
        "<b>Alert routing</b> to the right people by role, site and severity.",
        "<b>Alerts that are not about a camera</b> — a contractor's permit "
        "expiring, a visitor overstaying, a guard pressing panic — carry their own "
        "site, so a guarding-only agency with no cameras receives them, and they "
        "reach the people responsible for that site.",
        "<b>Incident management</b> — assignment, notes, severity, status and "
        "resolution, with bulk actions.",
        "<b>Command Centre</b> — the live operational picture across every site.",
        "<b>Action Centre</b> — cross-role duty guidance telling each person "
        "what needs them next.",
        "<b>Emergency broadcast</b> to all staff, a role or a site, with "
        "per-recipient acknowledgement tracking.",
        "<b>Dispatch</b> for sending a named guard and tracking arrival. The "
        "guard's own steps, the SLA clocks and escalation are module 20.",
        "<b>Notification channels and rules</b> — email, SMS, webhook, in-app and "
        "push, with a delivery log.",
    ])

story += module(
    "05", "Roster and scheduling",
    "Building the roster, and keeping it true when reality interferes.",
    [
        "<b>Shift patterns</b> per site, guard and weekday, expanded into "
        "concrete shifts on a rolling window.",
        "<b>Auto-scheduler</b> scoring candidates against nine rules: minimum "
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
        "<b>One post per guard.</b> An Operator or Security Guard holds one "
        "posting; Supervisors and Managers may cover several sites. The database "
        "holds the rule, so the same officer cannot be put on three sites at once.",
        "<b>What a site requires an officer to hold.</b> A tenant-wide baseline "
        "(every officer holds a valid licence) plus what a particular client's "
        "contract adds. An officer who does not meet it is reported daily — it is "
        "not a block, because a manager covering a 2 am no-show must not be "
        "stopped by the roster tool.",
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
    ])

story += module(
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
    ])

story += module(
    "07", "Guard operations",
    "What a guard actually does on shift, and the record it leaves.",
    [
        "<b>Guard tours</b> — routes and checkpoints scanned by QR or NFC, with "
        "tour schedules and compliance reporting.",
        "<b>Digital Occurrence Book</b> — entries with immutable timestamps, "
        "built against the Private Security Industry Act requirement. Fifteen "
        "kinds of entry. Search, supervisor review and correction are module 21.",
        "<b>Post orders</b> per site, with per-guard acknowledgement so "
        "there is a record of who read what. Versioned, approved procedures are "
        "module 22.",
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
    ])

story += module(
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
    ])

story += module(
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
        "<b>Contractors</b> — vetting, accreditation, work permits with approval "
        "and a safety briefing, and deliveries.",
        "<i>Being built now:</i> host approval of a visit, the places it is "
        "authorised for, escort, and what the gate is told — see <i>In progress "
        "and planned</i>.",
    ])

story += module(
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
    ])

story += module(
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
        "<b>PWM grade and wage floors.</b> The grade is on the officer record "
        "across the seven-grade ladder, and pay is checked against a floor: the "
        "statutory one, held once for every customer, or a company's own, which "
        "may only be higher. A floor is judged as of the payroll period, so "
        "restating March is judged against March. Where no rate has been loaded "
        "the result is <i>not assessed</i>, never <i>compliant</i>.",
        "<b>PLRD licence number and expiry</b> tracked per officer.",
        "<b>Payslip PDFs</b> and an <b>IR8A</b> annual summary and PDF.",
        "<i>Documented limitations:</i> graduated first- and second-year PR CPF "
        "rates and the Additional Wage ceiling are not implemented; no PWM rates "
        "are shipped — they are loaded from the published schedule with its source.",
    ])

story += module(
    "12", "Commercial",
    "Getting paid for the hours actually worked.",
    [
        "<b>Client management</b> with contacts and billing addresses.",
        "<b>Invoices generated from real attendance</b> — the loop closes from "
        "roster to check-in to worked hours to invoice line, per site.",
        "<b>Per-site bill rates</b> with overtime, tax and invoice PDFs.",
        "<b>Client portal</b> — the customer signs in and sees their own sites, "
        "incidents and invoices, and nothing else.",
        "<b>What the vendor charges the agency</b> — plans, module prices, "
        "subscriptions and the vendor's own invoices — is module 26.",
    ])

story += module(
    "13", "Compliance, evidence and reporting",
    "The record that survives an audit.",
    [
        "<b>Evidence management</b> with an access log recording every view and "
        "download. Sealed packages, holds and the full chain of custody are "
        "module 18.",
        "<b>PDPA</b> — consent records and data subject access requests, with "
        "erasure and subject export.",
        "<b>Contractor accreditation</b> and work permit tracking, with expiry "
        "warnings.",
        "<b>Data retention policies</b> per tenant, site and camera, and a hold "
        "that stops a purge.",
        "<b>Scheduled reports</b> delivered on a recurring basis by email or webhook.",
        "<b>Checksums on what is handed over</b> — patrol snapshots, patrol "
        "reports, recordings and evidence files — so a document can be checked "
        "later against what was written.",
        "<b>Exports</b> in operational formats, and a full analytics suite.",
    ])

story.append(PageBreak())

# ═══ PART 2 ═════════════════════════════════════════════════════════════════
story += part(
    "Part 2 — Added since the first edition",
    "Thirteen modules built after the first edition. Each was added beside what "
    "already worked: new tables, new API modules and new screens, with the "
    "existing ones left as they were. Every one keeps the same rule — the "
    "platform detects, relates, assesses and recommends, and a person decides.")

story += module(
    "14", "Virtual Patrolling",
    "A scheduled, camera-by-camera inspection carried out from a screen — the "
    "patrol a control room does without leaving it.",
    [
        "<b>Schedules</b> name a site, an ordered list of cameras and a set of "
        "questions. When its time arrives — in the site's own time zone — a "
        "session is created for the assigned officer.",
        "<b>A real snapshot at every camera.</b> The officer works through the "
        "cameras, a frame is captured from the live stream at each, and the "
        "questions are answered against it. Each snapshot carries a checksum.",
        "<b>What was asked is frozen into the session.</b> Editing a schedule "
        "mid-patrol does not change what the officer is being asked, and a patrol "
        "from March still reads as it did in March.",
        "<b>A failed answer can raise an incident</b> in the platform's own "
        "incident system.",
        "<b>One execution, one session</b> — enforced by the database, so two "
        "workers or a restart cannot create it twice. A patrol nobody did is "
        "marked missed.",
        "<b>A report for every patrol</b> — PDF and an Excel workbook, stored "
        "with a checksum and emailed at once or as a daily, weekly or monthly "
        "digest, each period sent once.",
        "<b>A Command Centre panel</b> for patrols due, running and missed.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATIONS 0116, 0117, 0121")

story += module(
    "15", "Drone Patrol",
    "An autonomous drone security patrol built into the platform rather than "
    "beside it: the drone's camera is one of the platform's cameras.",
    [
        "<b>Fleet, routes, zones and missions.</b> A drone is assigned to a site "
        "and flies a planned route on a schedule or on request, with pre-flight "
        "checks, a command queue, and maintenance logs.",
        "<b>The same AI watches it.</b> The detectors that watch fixed cameras "
        "watch the drone's. What they see is judged in the context of where the "
        "drone was and what that place is, and becomes a drone security event "
        "with a risk level.",
        "<b>Checked against fixed cameras.</b> The platform works out which fixed "
        "cameras could have seen the same thing, and whether they did.",
        "<b>Into the platform's own incident and dispatch.</b> A serious event "
        "becomes an incident, and a guard is sent through the existing dispatch.",
        "<b>Verify with drone.</b> An officer can ask for a flying look at "
        "something a fixed camera or a guard reported.",
        "<b>Site edge gateway.</b> A site can keep flying while its link to the "
        "platform is down: the gateway buffers what happened and syncs it exactly "
        "once when the link returns.",
        "<b>A record of every flight</b> — a PDF and a workbook, emailed on a "
        "schedule — with analytics, an analytical risk map and recommendations.",
        "<b>Licensed per tenant</b>, with limits on drones, missions and sites; "
        "sixteen permissions of its own; web and phone screens.",
        "<i>Documented limitation:</i> everything here has been proven against a "
        "simulator. No physical drone, manufacturer SDK, flight controller or real "
        "video stream has been connected.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATIONS 0123 TO 0130")

story += module(
    "16", "AI Security Intelligence and human decision support",
    "One flow over every source: events are brought to one shape, related into "
    "situations, assessed, and a response is suggested — and then a person "
    "decides.",
    [
        "<b>One shape for every security event</b> — CCTV, LPR, face, drone, "
        "virtual patrol, alarm, sensor, guard and system events — read from the "
        "tables that already hold them, without changing any of them.",
        "<b>What a site expects.</b> Site and camera profiles state what is normal "
        "there and when, and planned activity (a work permit, an expected visitor) "
        "is read as context.",
        "<b>Situations.</b> Events that belong together — the same plate, the same "
        "watchlist entry, neighbouring cameras moments apart — become one "
        "situation instead of several alerts.",
        "<b>How unusual, and how much it matters.</b> Each situation is assessed "
        "for normality and risk, with the reasons kept beside the score.",
        "<b>A suggestion, not an action.</b> The layer recommends a response and "
        "says what the recommendation rests on.",
        "<b>A person decides.</b> The suggestion, the human decision and the "
        "action carried out are three separate records. Only a decision made by a "
        "signed-in person causes anything to happen, and it happens through the "
        "platform's existing functions — dispatch, incident, a drone look.",
        "<b>Reports from the ground.</b> A guard on the phone accepts, arrives "
        "and reports what they found; a drone's look comes back as an event and "
        "the situation is assessed again.",
        "<b>A unified timeline and the evidence</b> of each situation, with an "
        "AI-assisted summary written from templates over recorded facts.",
        "<b>A site security score and daily intelligence</b> — where and in "
        "which hours situations begin, repeated vehicles and watchlist entries, "
        "and the share that turned out to be false alarms.",
        "<b>Feedback.</b> A person's review of what a closed situation turned out "
        "to be is kept, with the suggestion and the decision, as a dataset.",
        "<b>Off until asked.</b> A switch per tenant, off by default. If the "
        "background runner is stopped, alerts, incidents and video carry on "
        "exactly as before.",
        "<i>By design:</i> the background process cannot act — it reads, records "
        "and suggests, and tests hold that line. There is no language model. "
        "Nothing learns by itself: a rule or a weight changes only as a tenant "
        "setting or released code. A face that matched nobody is <i>unknown</i>, "
        "never <i>unauthorised</i>.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATIONS 0132 TO 0141")

story += module(
    "17", "Smart Investigation",
    "Finding what the platform already knows about a time, a place, a person "
    "or a vehicle — and keeping what was found.",
    [
        "<b>One search across fourteen sources</b> — by period, site, camera, "
        "kind, event type, severity, risk, plate, name, member of staff and words. "
        "Each source is read under its own existing permission and the asker's "
        "sites; a source left out is named, with the reason.",
        "<b>A typed phrase, read by fixed rules</b> into that same search. The "
        "screen shows which words became which filter and which were not used, so "
        "the person can correct it. A phrase with nothing understood is refused "
        "rather than guessed at.",
        "<b>Investigations.</b> A file of why somebody was looking and what they "
        "found: references to records of any of the fourteen kinds, with notes, in "
        "the order they happened, each read live as the reader may see it.",
        "<b>The trail of a plate or a watchlist face</b> — every sighting, in "
        "order, with the time and the distance between.",
        "<b>Every search is on the record</b> with what was asked, not what was found.",
        "<i>Documented limitation:</i> an unidentified person is not followed "
        "across cameras, and the platform does not claim to. There is no search by "
        "a picture of a face.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATION 0143")

story += module(
    "18", "Evidence packages and chain of custody",
    "What was kept about a matter: put together, sealed, held against deletion, "
    "and accounted for.",
    [
        "<b>What belongs is found and offered.</b> For an incident or an "
        "investigation: the frame of that detection, the recording of that camera "
        "at that moment, the drone media of that sighting. A person chooses what "
        "goes in.",
        "<b>Sealing.</b> A manifest of every item with its checksum is written, "
        "and the SHA-256 of the manifest. From then on a database trigger refuses "
        "every change.",
        "<b>Export</b> as a ZIP of the manifest as sealed and each original, byte "
        "for byte, with its checksum computed again and compared — plus a marked "
        "viewing copy of each picture.",
        "<b>One chain of custody per package</b>, from capture to release: "
        "captured, collected, accessed, sealed, placed under a hold, exported, "
        "downloaded, shared, released, hold lifted — each with who, in what role, "
        "and why.",
        "<b>Holds.</b> A hold stops all three retention jobs — frames and clips, "
        "recordings, drone media. Sealing places one on every item; a hold is "
        "lifted only by a person, with a reason.",
        "<i>Documented limitations:</i> video is not watermarked; nothing is "
        "digitally signed; the platform sends evidence to nobody — sharing and "
        "release are records of what a person did.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATION 0144")

story += module(
    "19", "The security map",
    "One operational map, and the places of a site as somebody who knows it "
    "drew them.",
    [
        "<b>Ten layers on one map</b> — sites, cameras, guards, open incidents, "
        "live alerts, open situations, drones, patrol checkpoints, places and "
        "drone zones — each shown under its own screen's existing permission and "
        "the reader's sites. What has no position is counted under the map, not "
        "hidden.",
        "<b>The places of a site.</b> Buildings, floors, gates, access points, "
        "emergency and assembly points, zones and parking — a point, an outline or "
        "a level of a building, drawn by an administrator. Optional: a site "
        "without them works as before. A place is retired, never removed.",
        "<b>A door on the map.</b> An access point can name a door the platform "
        "knows, which gives door events a place to appear.",
        "<b>What is near.</b> Selecting an incident, alert or situation lists the "
        "cameras, drones, checkpoints and places within a radius, and every guard "
        "on shift at the site, nearest first.",
        "<b>Where a guard last was.</b> The last position each guard recorded "
        "this shift — a check-in, a scan, a status change — always with its age, "
        "and marked stale past an hour.",
        "<i>By decision:</i> guards are not tracked continuously. <i>Documented "
        "limitations:</i> there are no floor plans; distances are straight lines, "
        "not routes; alarm panels and sensors are not drawn.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATION 0145")

story += module(
    "20", "Guard response, SLA and escalation",
    "A sending as its own record: what the guard did about it, how long it "
    "took, and who is told when it is late.",
    [
        "<b>The steps of a response.</b> Accepted, set off, arrived, reported — or "
        "cannot attend, with a reason — each written through to the incident's own "
        "status, history and arrival time. A guard who is not coming, or is stood "
        "down, gives the incident back.",
        "<b>The guard is told on their phone</b>, and answers there: Accept, On my "
        "way, I am there, Report what I found, I cannot attend.",
        "<b>Who to send — a suggestion with its reasons.</b> The guards on shift "
        "at the site, ranked by a score made of stated parts: free or already "
        "sent, distance by last recorded position and how old that position is, "
        "what they were already sent on this shift, and the certifications the "
        "site requires. A person chooses and dispatches.",
        "<b>Three clocks per incident</b> — acknowledge, arrive, resolve — "
        "judged every minute against the organisation's own settings. A clock that "
        "runs out is recorded once, marks the incident as breached, and is told to "
        "the person the settings name.",
        "<b>Escalation policies.</b> When an incident is still not acknowledged, "
        "reached or resolved after so long, tell a role or a person — by site and "
        "severity. Each step is recorded once, with how many people it reached.",
        "<b>A response desk</b> showing what each open incident is waiting for, "
        "with the procedure for it beside it.",
        "<i>By design:</i> the clocks are off until an organisation switches them "
        "on, and then apply only to incidents opened since. Nothing dispatches, "
        "reassigns or re-dispatches by itself.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATION 0146")

story += module(
    "21", "The occurrence book: reviewed, corrected and handed on",
    "The book read back — and what one shift hands the next.",
    [
        "<b>Search</b> by words, kind, site, author, shift, period, where its "
        "review stands, and whether it has been corrected.",
        "<b>Supervisor review.</b> An entry is noted, marked to be followed up, or "
        "its follow-up closed with what was done — by somebody other than who "
        "wrote it. Every review is kept. What a reviewer wrote is shown to the "
        "people who keep the book, not to a client or a viewer.",
        "<b>Correction by a further entry.</b> A mistake is put right by a new "
        "entry that records which entry it corrects and why. The first stays "
        "exactly as written.",
        "<b>Two more kinds of entry</b> — a delivery, and unusual activity.",
        "<b>Instructions in force at a site.</b> Issued with or without a date "
        "they run out on, read by each guard, closed with a reason. The guards on "
        "shift are told, and the instruction is carried into every shift's summary "
        "until it ends.",
        "<b>A shift's summary, drafted by the platform and confirmed by a "
        "person.</b> Written from what was recorded during the shift — the book, "
        "incidents, alerts, patrols, dispatches, visitors, what the site has in "
        "hand, instructions, follow-ups — in fixed sentences that count and quote. "
        "The guard reads it, corrects it and confirms it; what the platform "
        "drafted is kept beside it, and once confirmed it cannot be changed.",
        "<i>By design:</i> the summary writes no prose of its own and draws no "
        "conclusion; no language model is involved.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATION 0147")

story += module(
    "22", "The SOP library",
    "Procedures in versions, approved before they are in force, and found by "
    "their own words.",
    [
        "<b>A procedure and its versions.</b> A code it is cited by, a category, "
        "a site or every site. Versions are kept: drafted, submitted, approved to "
        "be in force from a date, or rejected with a reason.",
        "<b>Approved by somebody other than its author.</b> An approved version "
        "cannot be changed afterwards — a database trigger holds it.",
        "<b>In force, and running out.</b> Of the approved versions whose date "
        "has come, the latest is in force. A version that has run out leaves "
        "nothing in force rather than falling back to an older one.",
        "<b>The document as issued</b> can be attached to a version, kept with "
        "its SHA-256.",
        "<b>Asking the library</b> returns the passages of procedures in force "
        "that use the words asked — word for word, each with its procedure, its "
        "version and who approved it. When no passage uses those words it says so.",
        "<b>The procedure beside the incident.</b> A procedure names the kinds of "
        "incident it is for; the ones in force for an incident of that kind at its "
        "site are put beside it — on the response desk, on the phone's incident "
        "screen, and on the situation screen.",
        "<i>By design:</i> nothing composes an answer and no language model is "
        "involved — the platform never invents a procedure. <i>Documented "
        "limitations:</i> an attached PDF is kept and not read; the search reads "
        "English.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATION 0148")

story += module(
    "23", "Visitor and contractor authorisation",
    "Who said a visit may happen, for where, for how long and with whom "
    "— read at the gate, decided by the host.",
    [
        "<b>An authorisation of a visit or a work permit.</b> Asked for by whoever "
        "registers visitors; approved or declined by the host, or by somebody who "
        "manages visits when no host is named. A no says why.",
        "<b>For a period, and for places.</b> Valid from and until; for named "
        "places of the site map or for the site in general. Where it stands "
        "— waiting, valid, run out, never answered — is its answer and the hour, "
        "worked out when it is read.",
        "<b>Escort and ID.</b> Whether the visitor is to be escorted, and by whom; "
        "and the kind of ID a named person says they saw. The number on an ID is "
        "not taken, and the one on the visit is never returned.",
        "<b>What the gate reads.</b> What stands on the record, as sentences made "
        "from it. It informs: checking a visitor in is unchanged, and does not "
        "look at the authorisation.",
        "<b>Cancelled or extended</b> by the host or somebody who manages visits, "
        "each with a reason. The host is told on their phone when asked; whoever "
        "asked is told the answer.",
        "<b>Where a badge was used.</b> The door events of the badge a visitor was "
        "given, while they had it, are set against the places and the period the "
        "visit is authorised for. One outside either is listed for somebody who "
        "manages visits to look at, who records that it was in order or how it "
        "was followed up.",
        "<b>On the phone:</b> the visits waiting for your answer, and what stands "
        "for a visit beside its check-in.",
        "<i>By design:</i> an authorisation admits nobody and refuses nobody, and "
        "a door event outside it is not a finding — nothing is raised and nobody "
        "is accused. <i>Documented limitations:</i> a visitor is followed only "
        "through doors their badge number opened; the people working under a "
        "work permit are not followed; these events are not fed to the "
        "intelligence layer; nothing opens or locks a door.",
    ],
    tag="ADDED SINCE EDITION 2 · MIGRATION 0149")

story += module(
    "24", "Device health, assets and maintenance",
    "The health of devices as they report it, a register of what the "
    "organisation owns, and the work that keeps it working.",
    [
        "<b>One reading of every device</b> — camera, recorder, sensor, drone, "
        "edge gateway, alarm panel — from what each reports: working, degraded, "
        "down, not known or switched off, with why and since when.",
        "<b>What is not known is not called working.</b> A camera with no stream, "
        "a recorder not probed for a day, a device that has never reported. A "
        "sensor reading a dangerous value is a working sensor.",
        "<b>What is kept.</b> Every five minutes each device's state is read and "
        "kept when it has changed: the states it has been in, and how long it "
        "was down in the last week and month.",
        "<b>The asset register.</b> A code, a kind, make, model, serial number, "
        "where it is, vendor, installed, warranty, status. An asset may be the "
        "device it is and then shows that device's health; a UPS, a server or a "
        "switch is an asset with no reading, and is shown as that. Known devices "
        "are put into the register in one step. Retired, never removed.",
        "<b>Work orders.</b> Raised on an asset, for a facility defect or for a "
        "site; given to one of the organisation's people or a named vendor; "
        "started; completed with what was done, the parts used and the time out "
        "of use; or cancelled with a reason.",
        "<b>Preventive schedules.</b> Something done every so many days puts its "
        "work forward before each date.",
        "<b>Suggested, then accepted.</b> A schedule falling due — and, once an "
        "organisation asks for it, a device read as down for a set number of "
        "hours — puts a work order forward. It is a suggestion until somebody who "
        "manages maintenance accepts it or dismisses it with a reason; it "
        "assigns nobody and tells nobody.",
        "<i>By design:</i> every answer and screen that shows a reading names "
        "what is not measured. <i>Documented limitations:</i> frame rate, "
        "latency, packet loss, the quality of the picture and gaps in a "
        "recording are not measured; an outage shorter than five minutes can "
        "pass unrecorded; no cause is inferred; there is no parts store, cost or "
        "maintenance SLA; the phone has no part of it.",
    ],
    tag="ADDED SINCE EDITION 2 · MIGRATION 0150")

story += module(
    "25", "Risk patterns and advice",
    "Where what went wrong gathers — by hour, day, week and place — what stands "
    "out, how much history that rests on, and what a person made of it.",
    [
        "<b>Five kinds of thing that went wrong, counted the same way:</b> "
        "incidents; doors refused, forced or tampered with; patrols missed or "
        "failed — a guard's tour, a virtual patrol, a drone patrol; devices going "
        "down; response clocks missed.",
        "<b>Counted three ways,</b> over the last one to twelve weeks: by weekday "
        "and hour where the site is, by week, and by place — a camera, a door, a "
        "patrol, a device. Nobody is named.",
        "<b>A week of hours</b> for each kind, shaded in four steps with a key "
        "that says in counts what each shade stands for; every cell readable "
        "without its shade, and the numbers themselves on request.",
        "<b>What stands out, by fixed rules:</b> a band of four hours, a weekday "
        "or a place holding most of the records; twice as many in the later half "
        "of the period as in the earlier; a device down three times or more. "
        "Each statement has its count in it and one thing to consider.",
        "<b>How much history it rests on.</b> Much, some or little: how many "
        "records, over how many weeks, and in how many of those weeks the same "
        "held by itself. One busy afternoon is one week, not four.",
        "<b>Nothing before is not called a rise.</b> When the earlier half of a "
        "period has no records, the statement says that the counts cannot tell a "
        "change from the start of recording.",
        "<b>A person's answer.</b> For one site: accepted, or not accepted with a "
        "reason. The platform counts the advice again first and keeps its own "
        "statement with the answer. An answer is added and never rewritten.",
        "<i>By design:</i> a pattern that recurred is not a forecast, and every "
        "reading says so; confidence is how much history a statement rests on, "
        "not a probability; an answer raises no work, moves no guard and alters "
        "no roster. <i>Documented limitations:</i> no risk score is made; nothing "
        "allows for how much is watched at a place, so more records there is not "
        "more danger there; a record made while testing is counted like any "
        "other; places are not zones; there is no report or export; the phone "
        "has no part of it.",
    ],
    tag="ADDED SINCE EDITION 3 · MIGRATION 0151")

story += module(
    "26", "Platform operations — the vendor's console",
    "Seventh AI sells this to security companies, so the vendor and the "
    "customer are different businesses and do not share a login, a permission "
    "set or a bill.",
    [
        "<b>A platform tenant of its own.</b> The platform owner works from the "
        "vendor's tenant, not from inside a customer's, and holds none of a "
        "customer's day-to-day permissions — rosters, payroll, incidents, "
        "evidence. A customer's command centre is the customer's.",
        "<b>The customer's lifecycle</b> — trial, pending, active, suspended, "
        "expired, cancelled — rather than on or off, with a grace period after a "
        "subscription ends.",
        "<b>Pricing and licensing.</b> A catalogue of what each module costs and "
        "how it is charged, tied to the module licences the AI workers enforce.",
        "<b>The vendor's own invoices</b>, for customers who pay by bank transfer "
        "rather than by card.",
        "<b>Subscription notices</b> — a trial ending, a payment overdue, a "
        "licence about to lapse — each raised once, not nightly.",
        "<b>An error centre.</b> The same failure seen four thousand times is one "
        "row with a count: how many distinct things are broken, which customers "
        "each touches, since when, and whether anyone has dealt with it.",
        "<b>Support sessions, read-only by default.</b> The operator can look "
        "into a customer's account to answer a question without being able to "
        "change anything; elevated access is a separate, recorded choice.",
        "<b>Platform health and analytics</b> — recording, the intelligence "
        "runner, drone patrol; growth, adoption and churn from nightly snapshots.",
        "<b>The owner's account needs more than a password.</b> Two-factor "
        "authentication is enforced for the account that can reach every customer.",
        "<i>By design:</i> suspending a customer for non-payment is off by "
        "default. Cutting off a security company's cameras is a decision somebody "
        "makes deliberately, with their name on it.",
    ],
    tag="ADDED SINCE EDITION 1 · MIGRATIONS 0102 TO 0114")

story.append(PageBreak())

# ── Where AI is used ────────────────────────────────────────────────────────
story += [
    Paragraph("Where AI is used, and where a person decides", S["h1"]),
    rule(thickness=2),
    Spacer(1, 8),
    Paragraph(
        "\"AI\" covers several different things in this platform, and they are "
        "not trusted equally. This table says, for each place a model or a rule "
        "does work, what does it and what stays a person's. <b>It is the part of "
        "this document that is updated whenever an AI capability is added or "
        "upgraded</b> — with what the new capability does, what it is not trusted "
        "to do, and what a person still decides.", S["body"]),
]
story.append(data_table(
    ["Where", "What does the work today", "What a person does"],
    [
        ["Detection on camera and drone video (11 modules)",
         "Computer-vision models on frames: object detectors, a face embedding "
         "model, plate reading",
         "Reads the alert; acknowledges, escalates or dismisses it"],
        ["Face and plate watchlists",
         "Embedding similarity in the database (pgvector); plate text matching",
         "Keeps the watchlists; judges a match"],
        ["Roster auto-scheduler",
         "Scores candidates against nine stated rules. Not a learned model",
         "Reviews the draft, edits it, publishes it"],
        ["Security intelligence (module 16)",
         "Rules, versioned as a named engine: relate events into situations, "
         "score how unusual and how serious, suggest a response with its reasons",
         "Decides. Only a person's decision causes an action"],
        ["Drone events (module 15)",
         "Context rules and a risk engine over what the drone's camera detected; "
         "correlation with fixed cameras",
         "Decides what becomes an incident; asks for a drone look"],
        ["Who to send (module 20)",
         "A score made of stated parts, shown with the suggestion",
         "Chooses the guard and dispatches"],
        ["A typed investigation phrase (module 17)",
         "Fixed-rule parsing into a structured search, showing which words "
         "became which filter",
         "Corrects the search; reads the results"],
        ["Shift summary (module 21) and situation summary (module 16)",
         "Templates: fixed sentences over recorded counts and quotations",
         "Reads, corrects and confirms it"],
        ["Finding a procedure (module 22)",
         "Word search over approved text; passages quoted with their source",
         "Reads the procedure and acts on it"],
        ["A visitor's badge against what was authorised (module 23)",
         "A comparison: the door events of the badge with the places and the "
         "period of the authorisation. Not a model",
         "Looks at each one listed; records that it was in order or how it was "
         "followed up"],
        ["Device health (module 24)",
         "Rules over what each device reports: a stream's state, a probe, a "
         "heartbeat, a late reading. Nothing is inferred about the cause",
         "Reads it; decides what is work"],
        ["Work orders put forward (module 24)",
         "Two rules: a schedule falling due, and — when an organisation has "
         "switched it on — a device read as down for a set number of hours",
         "Accepts the suggestion as work, or dismisses it with a reason"],
        ["Risk patterns and advice (module 25)",
         "Fixed rules over counts of what was recorded: a band of hours, a "
         "weekday or a place holding most of the records; twice as many as "
         "before. Each says how much history it rests on. Not a model, and not "
         "a forecast",
         "Reads it; answers it for a site — accepted, or not accepted with a "
         "reason. The answer changes nothing else"],
        ["Daily intelligence and the site security score",
         "Counting over a stated period of history",
         "Reads it as history, not as a forecast"],
    ],
    widths=[44 * mm, 72 * mm, 54 * mm]))
story += [
    Spacer(1, 8),
    Paragraph("What the platform does not do today", S["h2"]),
]
story += bullets([
    "<b>No language model.</b> There is none in the platform and no dependency "
    "on one. Nothing is sent to an outside AI service. A typed question is "
    "parsed, not understood; a summary is a template, not prose.",
    "<b>Nothing decides for a person.</b> No part of the platform dispatches a "
    "guard, opens or closes an incident, flies a drone to an unplanned place, "
    "operates a door, admits or refuses a visitor, raises maintenance work, or "
    "changes a roster on a recommendation alone.",
    "<b>Nothing learns by itself.</b> A person's feedback on a situation is kept "
    "as a dataset; no model or rule is trained on it automatically. A rule or a "
    "weight changes only as a tenant setting or as released code.",
    "<b>Nobody is accused by a rule.</b> A face that matched nobody is "
    "<i>unknown</i>. An unidentified person is not followed across cameras. A "
    "prediction is a count over history, shown with the count under it.",
])
story.append(PageBreak())

# ── In progress and planned ─────────────────────────────────────────────────
#
# PLANNED: one row per piece of work that is not yet in the modules above. When
# it is merged and tested, delete its row here and write its module block.
story += [
    Paragraph("In progress and planned", S["h1"]),
    rule(thickness=2),
    Spacer(1, 8),
    Paragraph(
        "The enterprise expansion is fourteen phases, built and merged one at a "
        "time. Phases 1 to 9 are modules 17 to 25 above. What follows is not yet "
        "released and is <b>not counted</b> in any figure in this document. Each "
        "row moves into the numbered modules when it is merged and tested.", S["body"]),
]
story.append(data_table(
    ["Phase", "Area", "State", "What it adds"],
    [
        ["10", "Operations analytics and the daily briefing", "In progress",
         "Boards per role; a dated daily briefing a person reviews and publishes; "
         "the reports that are missing"],
        ["11", "Workforce intelligence", "Planned",
         "One reading per guard and per site; training and coverage "
         "recommendations for a manager. Never an employment decision, never a "
         "change to a roster"],
        ["12", "Case management", "Planned",
         "Cases from incidents and investigations: investigators, tasks, notes, "
         "linked evidence, approvals, a report"],
        ["13", "Compliance and hardening", "Planned",
         "One statement of every retention period in force; a report of what is "
         "held about a subject; a security sweep over everything the expansion added"],
        ["14", "Integration testing", "Planned",
         "The eleven end-to-end chains — from CCTV to incident, incident to "
         "evidence, evidence to case — as tests"],
    ],
    widths=[13 * mm, 37 * mm, 42 * mm, 78 * mm]))
story += [
    Spacer(1, 8),
    Paragraph("Waiting on something outside the code", S["h2"]),
]
story += bullets([
    "<b>A phone build and a device pass.</b> The phone parts of guard response, "
    "handover notes, procedures and visitor authorisation are type-checked and "
    "their rules tested, but "
    "have not been run on a device since the app moved to Expo SDK 57. Guards "
    "have them after a new build.",
    "<b>A new Windows desktop build.</b> Desktop 1.0.4 carries the screens up "
    "to AI Security Intelligence; the screens of modules 17 to 25 need a new "
    "build. The installer is not yet code-signed.",
    "<b>Real hardware.</b> A physical drone, access-control controllers and "
    "alarm panels have not been connected.",
    "<b>A cluster install.</b> The Helm chart lints and renders in CI and has "
    "not yet been installed on a cluster.",
])
story.append(PageBreak())

# ── Clients ─────────────────────────────────────────────────────────────────
story.append(Paragraph("Client applications", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))
story.append(data_table(
    ["Application", "Platform", "Scale", "Notes"],
    [
        ["Web console", "Browser (React)", "106 routes",
         "The full platform. Every module, every administrative surface."],
        ["Mobile app", "iOS and Android (React Native, Expo SDK 57)", "52 screens",
         "Built for the guard on shift: check-in, patrol scanning, occurrence "
         "book, handover, post orders, incidents and the response to them, "
         "situations, drone events, SOS. Offline outbox queues actions taken with "
         "no signal and sends them when it returns."],
        ["Windows desktop", "Electron", "Same as web",
         "Packaged installer for control-room machines, with auto-update. "
         "Version 1.0.4."],
    ],
    widths=[32 * mm, 38 * mm, 20 * mm, 80 * mm]))

story.append(Spacer(1, 10))
story.append(Paragraph("Roles", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "Eight built-in roles ship with the platform; tenants may define their own "
    "in addition. A role cannot grant a permission it does not itself hold. The "
    "permissions of modules 16 to 24 are held by the roles that work a site — "
    "not by Super Admin, and not by the Client role.",
    S["body"]))
story.append(data_table(
    ["Role", "Typical holder", "Scope"],
    [
        ["Super Admin", "Platform operator (the vendor)",
         "The platform: customers, pricing, licences, errors, support sessions. "
         "Not a customer's operations"],
        ["Admin", "Agency owner or operations director", "Everything within one tenant"],
        ["Manager", "Operations manager", "Multi-site oversight and approvals"],
        ["Supervisor", "Site or shift supervisor", "Rostering, attendance, review and incident response"],
        ["Operator", "Control room operator", "Live monitoring, alerts, decisions and dispatch"],
        ["Security Guard", "Officer on shift", "Mobile app: own shifts, patrols, responses and reports"],
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
    "Act requirement for a licensed agency to maintain one. No screen or "
    "endpoint edits or removes an entry; a mistake is corrected by a further entry.",
    "<b>CPF</b> calculated by age band against the S$7,400 Ordinary Wage ceiling, "
    "with eligibility determined by work pass type.",
    "<b>IR8A</b> annual reporting.",
    "<b>PWM grades</b> across the seven-grade Progressive Wage Model ladder, with "
    "wage floors that pay is checked against, and <b>PLRD licence</b> number and "
    "expiry per officer.",
    "<b>What a site requires an officer to hold</b>, with a daily report of who "
    "does not meet it.",
    "<b>Gazetted public holidays</b> with holiday pay, and the 72-hour monthly "
    "overtime cap flagged on every payroll run.",
    "<b>SGD invoicing</b> with purchase-order and payment-terms fields.",
    "<b>Work pass tracking</b> with expiry warnings.",
    "<b>PDPA</b> consent and data subject request handling; identity numbers kept "
    "to the minimum each record needs.",
    "<b>Asia/Singapore</b> as the tenant timezone, applied to every rendered date.",
    "<b>Four languages</b> reflecting the local workforce: English, Chinese, "
    "Malay and Tamil.",
])

story.append(Spacer(1, 10))
story.append(Paragraph("Known limits", S["h1"]))
story.append(rule(thickness=2))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "Stated here in one place, as well as beside each module, because a "
    "reference that leaves them out is a brochure.", S["body"]))
story += bullets([
    "<b>Drone Patrol has flown only in a simulator.</b>",
    "<b>Access control and alarm panels</b> have no real hardware behind them on "
    "the development system.",
    "<b>There is no language model.</b> Questions are parsed by fixed rules; "
    "summaries are templates; a procedure is found by its words.",
    "<b>An unidentified person cannot be followed across cameras.</b> A plate "
    "can; a watchlist entry can.",
    "<b>Guards are not tracked continuously.</b> A guard's position is the last "
    "one they recorded, shown with its age.",
    "<b>Device health is what devices report.</b> Stream state, disconnections, "
    "probes, heartbeats and late readings are read; frame rate, latency, packet "
    "loss, the quality of the picture and gaps in a recording are not measured, "
    "and an outage shorter than five minutes can pass unrecorded.",
    "<b>A visitor is followed only through the doors their badge opened</b>, and "
    "those door events are not fed to the intelligence layer.",
    "<b>A work order put forward by the platform</b> says what was read, not what "
    "is wrong: no cause is inferred for a device that is down.",
    "<b>Risk patterns are counts over history</b>, not forecasts. No risk score is "
    "made, nothing allows for how much is watched at a place, and a record made "
    "while testing is counted like any other.",
    "<b>Payroll:</b> graduated first- and second-year PR CPF rates and the "
    "Additional Wage ceiling are not implemented; PWM rates are not shipped.",
    "<b>Evidence:</b> video is not watermarked and nothing is digitally signed.",
    "<b>The phone app</b> needs a new build and a device pass for what was added "
    "after its SDK upgrade; <b>the desktop app</b> needs a new build for modules "
    "17 to 25 and is not code-signed.",
    "<b>The Helm chart</b> has not been installed on a cluster.",
])

story.append(Spacer(1, 14))
story.append(rule(thickness=0.7, color=RULE))
story.append(Spacer(1, 6))
story.append(Paragraph(
    f"Compiled from the Seventh AI Vision source repository at migration {HEAD} — 151 "
    "database migrations, 274 tables, 102 API modules serving 997 operations, 104 backend "
    "services, 106 web routes and 52 phone screens, covered by 5,920 automated tests "
    "(backend 3,955, repository inspection 1,276, web 406, phone 283). Capability counts "
    "reflect what is merged and running, not what is in progress or planned.", S["foot"]))

doc.build(story)
print("WROTE", OUT)
