"""
7th AI Vision — Professional Product Presentation Builder
Generates a polished enterprise PPT from app screenshots.
"""
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt
from PIL import Image
import io

# ── Palette ─────────────────────────────────────────────────────────────────
BG_DARK      = RGBColor(0x08, 0x08, 0x18)   # #080818
BG_PANEL     = RGBColor(0x10, 0x10, 0x28)   # #101028
VIOLET       = RGBColor(0x6C, 0x63, 0xFF)   # #6C63FF
TEAL         = RGBColor(0x00, 0xD9, 0xC0)   # #00D9C0
WHITE        = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY   = RGBColor(0xCC, 0xCC, 0xDD)
MID_GRAY     = RGBColor(0x88, 0x88, 0xAA)
ACCENT_RED   = RGBColor(0xFF, 0x45, 0x60)
ACCENT_AMBER = RGBColor(0xFF, 0x98, 0x00)
ACCENT_GREEN = RGBColor(0x00, 0xE3, 0x96)

SCRS = "D:\\Virtual Patrolling\\scripts\\ppt_screenshots"
OUT  = "D:\\Virtual Patrolling\\scripts\\7th_AI_Vision_Product_Presentation.pptx"

prs = Presentation()
prs.slide_width  = Inches(13.33)
prs.slide_height = Inches(7.5)

BLANK = prs.slide_layouts[6]   # totally blank


# ── Helpers ──────────────────────────────────────────────────────────────────

def bg(slide, color=BG_DARK):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color

def box(slide, l, t, w, h, fill_color=None, alpha_hint=None):
    shape = slide.shapes.add_shape(1, Inches(l), Inches(t), Inches(w), Inches(h))
    shape.line.fill.background()
    if fill_color:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_color
    else:
        shape.fill.background()
    return shape

def gradient_box(slide, l, t, w, h, c1, c2):
    """Fake gradient with two overlapping boxes at 50% each."""
    s1 = slide.shapes.add_shape(1, Inches(l), Inches(t), Inches(w/2), Inches(h))
    s1.fill.solid(); s1.fill.fore_color.rgb = c1; s1.line.fill.background()
    s2 = slide.shapes.add_shape(1, Inches(l+w/2), Inches(t), Inches(w/2), Inches(h))
    s2.fill.solid(); s2.fill.fore_color.rgb = c2; s2.line.fill.background()

def txt(slide, text, l, t, w, h, size=18, bold=False, color=WHITE,
        align=PP_ALIGN.LEFT, wrap=True, italic=False):
    txBox = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = txBox.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    return txBox

def divider(slide, l, t, w, color=VIOLET, thickness=0.03):
    shape = slide.shapes.add_shape(1, Inches(l), Inches(t), Inches(w), Inches(thickness))
    shape.fill.solid(); shape.fill.fore_color.rgb = color; shape.line.fill.background()

def screenshot(slide, filename, l, t, w, h):
    path = os.path.join(SCRS, filename)
    if os.path.exists(path):
        slide.shapes.add_picture(path, Inches(l), Inches(t), Inches(w), Inches(h))

def badge(slide, text, l, t, color=VIOLET, text_color=WHITE, size=11):
    w = len(text) * 0.085 + 0.25
    shape = slide.shapes.add_shape(
        9,  # rounded rectangle
        Inches(l), Inches(t), Inches(w), Inches(0.28)
    )
    shape.fill.solid(); shape.fill.fore_color.rgb = color; shape.line.fill.background()
    tf = shape.text_frame
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    run = tf.paragraphs[0].add_run()
    run.text = text; run.font.size = Pt(size); run.font.bold = True
    run.font.color.rgb = text_color
    return w

def bullet_list(slide, items, l, t, w, h, size=13, color=LIGHT_GRAY, bullet="▸"):
    txBox = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_before = Pt(4)
        p.alignment = PP_ALIGN.LEFT
        run = p.add_run()
        run.text = f"{bullet}  {item}"
        run.font.size = Pt(size)
        run.font.color.rgb = color


# ════════════════════════════════════════════════════════════════════════════
# SLIDE 1 — Title
# ════════════════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
bg(s)

# Full background gradient stripe
gradient_box(s, 0, 0, 13.33, 7.5, BG_DARK, BG_PANEL)

# Hero violet stripe left
shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
shape.fill.solid(); shape.fill.fore_color.rgb = VIOLET; shape.line.fill.background()

# Shield icon placeholder (large decorative circle)
circ = s.shapes.add_shape(9, Inches(8.8), Inches(1.0), Inches(4.0), Inches(4.0))
circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(0x20, 0x18, 0x50)
circ.line.color.rgb = VIOLET; circ.line.width = Pt(2)

# Decorative teal ring
circ2 = s.shapes.add_shape(9, Inches(9.3), Inches(1.5), Inches(3.0), Inches(3.0))
circ2.fill.background(); circ2.line.color.rgb = TEAL; circ2.line.width = Pt(1)

# Shield text "7"
txt(s, "7", 10.1, 2.1, 1.5, 1.5, size=80, bold=True, color=VIOLET, align=PP_ALIGN.CENTER)
txt(s, "th", 11.2, 2.1, 0.8, 0.6, size=32, bold=True, color=TEAL, align=PP_ALIGN.LEFT)

# Product name
txt(s, "7th AI Vision", 0.4, 1.2, 8.0, 1.2, size=52, bold=True, color=WHITE)

# Tagline with violet gradient hint
txt(s, "Enterprise AI Video Surveillance Platform", 0.4, 2.45, 8.5, 0.7, size=24, color=TEAL)

divider(s, 0.4, 3.3, 6.5, VIOLET)

# Description
desc = (
    "A multi-tenant, GPU-accelerated security SaaS platform featuring 11+ AI detection "
    "modules, real-time alert push, guard operations management, and full compliance tools — "
    "deployable on Windows via native desktop app or web browser."
)
txt(s, desc, 0.4, 3.5, 8.2, 1.5, size=15, color=LIGHT_GRAY)

# Feature badges row
badges = ["11 AI Modules", "Multi-Tenant", "Real-Time Push", "PDPA Compliant", "Windows Desktop"]
bx = 0.4
for b in badges:
    w = badge(s, b, bx, 5.3, VIOLET)
    bx += w + 0.2

# Stats row
stats = [("104", "Tests Passing"), ("11+", "AI Modules"), ("10", "Cameras"), ("3", "Sites")]
sx = 0.4
for val, lbl in stats:
    txt(s, val, sx, 6.0, 2.0, 0.7, size=32, bold=True, color=TEAL, align=PP_ALIGN.LEFT)
    txt(s, lbl, sx, 6.65, 2.0, 0.4, size=11, color=MID_GRAY, align=PP_ALIGN.LEFT)
    sx += 2.5

txt(s, "Confidential — For Demonstration Purposes Only", 0.4, 7.1, 12.5, 0.35,
    size=9, color=MID_GRAY, italic=True)


# ════════════════════════════════════════════════════════════════════════════
# SLIDE 2 — Platform Overview
# ════════════════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
bg(s)
shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
shape.fill.solid(); shape.fill.fore_color.rgb = TEAL; shape.line.fill.background()

txt(s, "Platform Overview", 0.4, 0.25, 10.0, 0.65, size=32, bold=True, color=WHITE)
divider(s, 0.4, 0.95, 12.5, TEAL)

modules = [
    ("🔍  License Plate Recognition", "Detect, read and match vehicle plates against block/allow watchlists in real time"),
    ("👤  Face Recognition",          "ArcFace 512-d embeddings, VIP & blacklist matching, unknown-face alerting"),
    ("🚧  Intrusion Detection",        "Restricted zone breach detection with polygon ROI, dwell-time tracking & SLA escalation"),
    ("🦺  PPE Compliance",            "Hard-hat, vest, and safety-gear enforcement per camera zone"),
    ("👥  Crowd Density Analysis",     "Real-time head-count, density heatmap, threshold-triggered alerts"),
    ("🔥  Fire & Smoke Detection",     "Early fire/smoke warning with high-confidence multi-frame confirmation"),
    ("🔫  Weapon Detection",           "Firearm and bladed-weapon detection with immediate critical alert + incident"),
    ("📊  Behavior Analysis",          "Loitering, running, fighting, and abnormal motion classification"),
    ("📷  Camera Tampering",           "Detects spray, obstruction, and camera repositioning attacks"),
    ("📦  Abandoned Object",           "Flags unattended items exceeding configurable dwell thresholds"),
    ("🏃  Slip / Fall Detection",      "Detects falls in elderly-care, warehouse and high-risk environments"),
]

col_w = 6.0
for i, (name, desc) in enumerate(modules):
    col = i % 2
    row = i // 2
    lx = 0.4 + col * col_w
    ty = 1.1 + row * 0.97
    panel = s.shapes.add_shape(1, Inches(lx), Inches(ty), Inches(col_w - 0.2), Inches(0.82))
    panel.fill.solid(); panel.fill.fore_color.rgb = BG_PANEL; panel.line.fill.background()
    txt(s, name, lx + 0.1, ty + 0.03, col_w - 0.4, 0.35, size=12, bold=True, color=VIOLET)
    txt(s, desc, lx + 0.1, ty + 0.38, col_w - 0.4, 0.4, size=10, color=LIGHT_GRAY)

txt(s, "All modules share a common Redis Streams pipeline, PostgreSQL RLS tenant isolation, real-time WebSocket push, and evidence chain-of-custody.",
    0.4, 6.8, 12.5, 0.5, size=10, color=MID_GRAY, italic=True)


# ════════════════════════════════════════════════════════════════════════════
# Slide builder helper
# ════════════════════════════════════════════════════════════════════════════
def feature_slide(title, subtitle, filename, bullets, accent=VIOLET, badge_labels=None):
    s = prs.slides.add_slide(BLANK)
    bg(s)
    # Left accent bar
    shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
    shape.fill.solid(); shape.fill.fore_color.rgb = accent; shape.line.fill.background()

    # Header panel
    hdr = s.shapes.add_shape(1, Inches(0.12), Inches(0), Inches(13.21), Inches(1.05))
    hdr.fill.solid(); hdr.fill.fore_color.rgb = BG_PANEL; hdr.line.fill.background()

    txt(s, title, 0.3, 0.05, 9.0, 0.55, size=26, bold=True, color=WHITE)
    txt(s, subtitle, 0.3, 0.62, 9.5, 0.38, size=13, color=accent)

    # Page tag (top right)
    txt(s, "7th AI Vision", 10.5, 0.1, 2.7, 0.4, size=11, color=MID_GRAY, align=PP_ALIGN.RIGHT)

    # Screenshot (right pane)
    scr_l, scr_t, scr_w, scr_h = 6.55, 1.1, 6.65, 5.8
    # Screenshot border
    frame = s.shapes.add_shape(1, Inches(scr_l - 0.05), Inches(scr_t - 0.05),
                                Inches(scr_w + 0.1), Inches(scr_h + 0.1))
    frame.fill.solid(); frame.fill.fore_color.rgb = RGBColor(0x25, 0x20, 0x50)
    frame.line.color.rgb = accent; frame.line.width = Pt(1)
    screenshot(s, filename, scr_l, scr_t, scr_w, scr_h)

    # Left pane: bullets
    divider(s, 0.3, 1.1, 5.9, accent)
    bullet_list(s, bullets, 0.3, 1.25, 5.9, 4.8, size=13, color=LIGHT_GRAY)

    # Optional badge labels
    if badge_labels:
        bx = 0.3
        for bl in badge_labels:
            c = TEAL if badge_labels.index(bl) % 2 == 0 else VIOLET
            w = badge(s, bl, bx, 6.25, c, WHITE, 10)
            bx += w + 0.15

    divider(s, 0.3, 7.1, 12.7, RGBColor(0x33, 0x33, 0x55))
    txt(s, "Enterprise AI Video Surveillance — 7th AI Vision  |  Confidential",
        0.3, 7.15, 12.5, 0.3, size=8, color=MID_GRAY)
    return s


# ════════════════════════════════════════════════════════════════════════════
# SLIDE 3 — Dashboard
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Live Security Dashboard",
    "Real-time situational awareness across all sites, cameras, and AI modules",
    "01_dashboard.png",
    [
        "Live KPI cards: Open Alerts, Incidents, Active Cameras, Detections today",
        "Site Health overview with per-site camera online status",
        "7-day detection trend chart — all modules visualised",
        "Alert severity breakdown: Critical / High / Medium / Low",
        "Active notification channels display (email, webhook, SMS)",
        "Real-time WebSocket push — counts update instantly on new events",
        "Role-based view: Guards see assigned shifts; Admins see full tenant stats",
        "Dark glassmorphism UI — optimised for 24/7 control room environments",
    ],
    VIOLET,
    ["Real-Time", "Multi-Site", "Role-Based", "WebSocket Push"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 4 — Alerts
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Smart Alert Management",
    "AI-generated alerts with severity triage, module filtering, and one-click acknowledgement",
    "02_alerts.png",
    [
        "Alerts auto-generated by all 11 AI detection modules",
        "Severity levels: Critical (red), High (orange), Medium, Low, Info",
        "Filter by module: LPR, Face, Intrusion, PPE, Crowd, Fire, Weapon, Behavior, Tamper…",
        "Acknowledge or mark False Positive directly from the alert list",
        "Real-time badge counter updates as new alerts arrive via WebSocket",
        "Alert deduplication — no repeat alerts within configurable cooldown window",
        "Structured alert codes (e.g. lpr.blocklist_hit) for i18n-ready rendering",
        "Auto-escalation: unacknowledged High/Critical alerts escalate after SLA deadline",
    ],
    ACCENT_RED,
    ["Auto-Generated", "11 Modules", "False Positive Flag", "SLA Escalation"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 5 — Incidents
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Incident Management & Workflow",
    "From alert to resolution — full incident lifecycle with assignment, notes, and audit trail",
    "03_incidents.png",
    [
        "Auto-created incidents for high/critical severity detections",
        "Extended workflow states: Open → Acknowledged → In Progress → Escalated → Resolved",
        "Assign incident to any operator or supervisor user",
        "Incident notes timeline with timestamps and author tracking",
        "Multi-camera correlation: one incident can link multiple camera events",
        "Evidence chain-of-custody: snapshot images linked to each incident",
        "Dispatch integration: send guard to scene with ETA and custody tracking",
        "PDPA-compliant incident closure with data subject references",
    ],
    ACCENT_AMBER,
    ["AUTO Badge", "Multi-Camera", "Dispatch", "Full Audit Trail"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 6 — Live Wall
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Live Wall — Multi-Camera Viewer",
    "Watch up to 16 simultaneous live streams in configurable grid layouts",
    "04_live_wall.png",
    [
        "Grid layouts: 1×1, 2×2, 3×3, 4×4 — up to 16 simultaneous live views",
        "Site-filtered camera picker — quickly add cameras to the wall",
        "Camera name + site label overlay on each cell",
        "Per-cell: Record button (start/stop clip), Full-screen expand, Remove",
        "MJPEG live streaming proxy — works through the same web/app server",
        "Recording: streams saved as MP4, listed in Recordings page for download",
        "Wall layout persisted to localStorage — survives refresh",
        "Available in both web dashboard and Windows desktop application",
    ],
    TEAL,
    ["16 Streams", "MJPEG Live", "MP4 Recording", "Persistent Layout"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 7 — Sites & Cameras
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Sites & Camera Management",
    "Hierarchical site → camera structure with RTSP stream validation and per-module AI tagging",
    "06_cameras.png",
    [
        "Sites group cameras by physical location (HQ Building, Warehouse, Car Park…)",
        "Per-camera AI module tags: lpr, face, intrusion, ppe, crowd, fire_smoke, weapon…",
        "Camera active/inactive status with real-time health monitoring",
        "RTSP stream URL + credential storage with live 'Test Connection' validation",
        "Connection validation returns resolution, FPS, and latency before saving",
        "Stream health monitoring: online → degraded → offline state machine",
        "Camera offline alerts pushed to notification channels automatically",
        "Per-tenant module licensing controls which AI features each camera can use",
    ],
    VIOLET,
    ["3 Sites", "10 Cameras", "RTSP Validation", "AI Module Tags"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 8 — AI Detections
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "AI Detection Engine — 11 Modules",
    "GPU-accelerated per-module workers with independent consumer groups and crash recovery",
    "08_detections_lpr.png",
    [
        "LPR: YOLOv8 plate detection + PaddleOCR — reads plates at ≥55% confidence",
        "Face: InsightFace buffalo_l — 512-d ArcFace embeddings, cosine similarity match",
        "Intrusion: YOLOv8 person + Shapely polygon zone check, foot-point method",
        "PPE / Crowd / Fire / Smoke / Weapon / Behavior / Tamper / Abandoned / Fall",
        "Each module runs as an independent Docker service — scales GPU workers independently",
        "Redis Streams pipeline: XADD frames → XCONSUME per-module group → write DB",
        "Crash recovery: XPENDING → XCLAIM — no frame silently lost on worker restart",
        "Tenant-configurable thresholds via admin settings — no redeploy required",
    ],
    TEAL,
    ["YOLOv8", "InsightFace", "Redis Streams", "GPU Ready"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 9 — Watchlists
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Smart Watchlist Management",
    "Plate and face watchlists with block/allow classification and real-time matching",
    "09_watchlists.png",
    [
        "Plate watchlist: block (stolen/suspect) and allow (VIP/authorised) entries",
        "Block hits → Critical alert + auto-incident; Allow hits → Low info alert only",
        "Face watchlist: 512-d ArcFace vector embeddings stored per person",
        "Face match threshold configurable per tenant (default 60% cosine similarity)",
        "Unrecognised faces generate low-severity 'Info' alerts — no noise suppression",
        "Entries include reason text and expiry date for time-limited blocks",
        "Police case reference fields for evidentiary integration",
        "All watchlist queries tenant-scoped via PostgreSQL RLS — cross-tenant leakage impossible",
    ],
    ACCENT_RED,
    ["Block / Allow", "ArcFace 512-d", "Real-Time Match", "Case Reference"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 10 — Restricted Zones
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Restricted Zone Configuration",
    "Polygon-based exclusion zones per camera with severity levels and dwell-time tracking",
    "10_zones.png",
    [
        "Draw polygon zones on camera view — normalised 0..1 coordinates stored as JSON",
        "Severity per zone: Low, Medium, High, Critical — drives alert severity",
        "High/Critical zones auto-create incidents; Low/Medium alert only",
        "Redis-backed BreachTracker deduplicates — one alert per breach episode, not per frame",
        "Sliding TTL cooldown: person lingering keeps the breach active, doesn't re-alert",
        "Dwell-time tracking: how long a person was in the zone (seconds)",
        "8 zones seeded across 3 sites: Main Gate, Server Room, Loading Dock, Cold Storage…",
        "Zones per camera; multiple zones supported simultaneously on same feed",
    ],
    ACCENT_AMBER,
    ["Polygon ROI", "4 Severity Levels", "Dwell Tracking", "Deduplication"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 11 — Analytics
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Analytics & Reporting Dashboard",
    "30-day trend analysis across detections, alerts, incidents, and module breakdowns",
    "11_analytics.png",
    [
        "KPI row: Total detections (30d), Total alerts, Open alerts, Incident resolution count",
        "Detections by day — stacked bar chart per AI module",
        "Alerts by day — trend line with severity overlay",
        "Alerts by severity: Critical / High / Medium / Low breakdown",
        "Alerts by module — which AI modules are generating the most events",
        "Incident resolution time trend — SLA compliance monitoring",
        "All queries filterable by site and date range",
        "Export to CSV/Excel for compliance and management reporting",
    ],
    TEAL,
    ["30-Day Trends", "Module Breakdown", "SLA Monitoring", "CSV Export"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 12 — Activity Heatmap
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Activity Heatmap — Spatial Intelligence",
    "Visual site map showing camera positions, detection density, and live alert status",
    "14_heatmap.png",
    [
        "Camera positions shown on a floor-plan / spatial canvas",
        "Circle size = detection volume over selected time period",
        "Colour coding: Red (critical), Orange (high), Yellow (open alerts), Teal (activity)",
        "Pulsing ring animation = active critical alert on that camera",
        "Filter by: Time Period (Last 24h, 7d, 30d), Module, Site",
        "Top Cameras by Alerts leaderboard — identify hotspots instantly",
        "Summary badges: total detections, total alerts, critical count",
        "Instant navigation: click a camera bubble to open live feed or alert list",
    ],
    VIOLET,
    ["Spatial View", "Alert Bubbles", "Critical Pulse", "Site Filter"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 13 — Guard Operations
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Guard Operations Management",
    "Shift scheduling, patrol routing, checkpoint scanning, and visitor management",
    "12_guard_ops.png",
    [
        "Shift management: assign guards to sites with start/end times and handover notes",
        "Patrol routes: define waypoints with QR-code checkpoints for verification",
        "Mobile QR scanner: guard scans checkpoint, system logs GPS + timestamp proof",
        "SOS panic button on mobile app — immediate critical incident created",
        "Daily Occurrence Book (DOB): printable PLRD-compliant handover report",
        "Visitor management: pre-registration, arrival/departure logging, overstay alerts",
        "Overstay scheduler: auto-alert when visitor exceeds permitted duration",
        "Dispatch: assign guard to incident with ETA, custody chain tracking",
    ],
    ACCENT_GREEN,
    ["Shift Mgmt", "QR Checkpoints", "DOB Report", "Visitor Log"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 14 — Reports & Compliance
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Reports & Compliance",
    "PDF report generation, PDPA compliance tools, and DSAR request management",
    "13_reports.png",
    [
        "Site Summary Report: alerts, incidents, camera stats over configurable date range",
        "Daily Occurrence Book (DOB): printable extract for PLRD compliance and handover",
        "Incident Detail Report: full report with evidence chain of custody and timeline",
        "PDPA / DSAR tab: manage Data Subject Access Requests with 30-day response tracking",
        "Privacy masking: configurable face/plate blurring for compliance exports",
        "All reports tenant-scoped — operators cannot access other tenants' data",
        "Blockchain-style audit log verification (Verify Chain button)",
        "Export history audit — every download logged with user, IP, timestamp",
    ],
    TEAL,
    ["PDF Reports", "PDPA/DSAR", "Privacy Masking", "Chain Verify"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 15 — Notifications
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Multi-Channel Notification System",
    "Email, SMS, and webhook delivery with rule-based routing and delivery logs",
    "16_notifications.png",
    [
        "Channels: Email (SMTP), SMS (Twilio), Webhook (Slack, Teams, custom)",
        "3 active channels seeded: Ops Email Alert, Slack Security Ops, SMS On-Call Guard",
        "Rule engine: route specific alert types/severities to specific channels",
        "Delivery logs: track every notification sent with status and timestamp",
        "Test endpoint: fire a test notification to verify channel config",
        "Mobile push notifications: Expo FCM/APNs for iOS and Android apps",
        "Camera offline alerts automatically dispatched when connectivity drops",
        "Notification rules configurable per tenant by admin without code changes",
    ],
    VIOLET,
    ["Email / SMS / Webhook", "Mobile Push", "Delivery Logs", "Rule Engine"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 16 — Administration
# ════════════════════════════════════════════════════════════════════════════
feature_slide(
    "Administration & Security",
    "Multi-tenant management, RBAC, 2FA, API keys, IP allowlist, and session control",
    "18_users.png",
    [
        "6 RBAC roles: Super Admin, Admin, Supervisor, Operator, Security Guard, Viewer",
        "Per-tenant module licensing: enable/disable AI modules per tenant (8/11 active)",
        "Two-factor authentication (TOTP) — enforced per-tenant by admin policy",
        "Active session management: view and revoke sessions across devices",
        "Per-account login lockout after configurable failed attempts",
        "API key management with scoped permissions for programmatic access",
        "IP allowlist: restrict tenant access to approved CIDR ranges only",
        "All admin actions logged in tamper-evident audit trail (Verify Chain)",
    ],
    ACCENT_AMBER,
    ["6 RBAC Roles", "2FA/TOTP", "IP Allowlist", "Session Mgmt"],
)

# ════════════════════════════════════════════════════════════════════════════
# SLIDE 17 — Windows Desktop App
# ════════════════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
bg(s)
shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
shape.fill.solid(); shape.fill.fore_color.rgb = VIOLET; shape.line.fill.background()

hdr = s.shapes.add_shape(1, Inches(0.12), Inches(0), Inches(13.21), Inches(1.05))
hdr.fill.solid(); hdr.fill.fore_color.rgb = BG_PANEL; hdr.line.fill.background()
txt(s, "Windows Desktop Application", 0.3, 0.05, 9.0, 0.55, size=26, bold=True, color=WHITE)
txt(s, "Native Electron app — full platform capability without a browser", 0.3, 0.62, 9.5, 0.38, size=13, color=VIOLET)

screenshot(s, "01_dashboard.png", 6.55, 1.1, 6.65, 5.8)
frame = s.shapes.add_shape(1, Inches(6.5), Inches(1.05), Inches(6.75), Inches(5.9))
frame.fill.background(); frame.line.color.rgb = VIOLET; frame.line.width = Pt(1)

divider(s, 0.3, 1.1, 5.9, VIOLET)
bullet_list(s, [
    "Packaged as native Windows .exe (NSIS installer + portable)",
    "First-run wizard: configure server URL (local or cloud deployment)",
    "System tray icon with real-time unread alert badge count",
    "Native Windows notifications for Critical/High severity alerts",
    "Auto-reconnect WebSocket with exponential backoff",
    "Offline mode indicator with graceful degradation",
    "No browser required — runs alongside other security software",
    "Electron wrapper reuses 100% of the React web frontend — zero code duplication",
    "Optional auto-start on Windows login via system settings toggle",
], 0.3, 1.25, 5.9, 5.5, size=13)

badge(s, "Electron", 0.3, 6.85, VIOLET)
badge(s, "NSIS Installer", 2.0, 6.85, TEAL)
badge(s, "System Tray", 3.9, 6.85, VIOLET)
badge(s, "Native Push", 5.5, 6.85, TEAL)

txt(s, "Enterprise AI Video Surveillance — 7th AI Vision  |  Confidential",
    0.3, 7.15, 12.5, 0.3, size=8, color=MID_GRAY)


# ════════════════════════════════════════════════════════════════════════════
# SLIDE 18 — Architecture
# ════════════════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
bg(s)
shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
shape.fill.solid(); shape.fill.fore_color.rgb = TEAL; shape.line.fill.background()

hdr = s.shapes.add_shape(1, Inches(0.12), Inches(0), Inches(13.21), Inches(1.05))
hdr.fill.solid(); hdr.fill.fore_color.rgb = BG_PANEL; hdr.line.fill.background()
txt(s, "Technical Architecture", 0.3, 0.05, 9.0, 0.55, size=26, bold=True, color=WHITE)
txt(s, "Production-ready microservice architecture — Docker Compose → Kubernetes ready", 0.3, 0.62, 10.0, 0.38, size=13, color=TEAL)

layers = [
    ("Clients",         "Web (React+MUI)  •  iOS/Android (Expo)  •  Windows (Electron)",        VIOLET),
    ("API Gateway",     "FastAPI /api/v1  •  Nginx reverse proxy  •  SlowAPI rate limiting",     TEAL),
    ("Real-Time",       "WebSocket /ws/live  •  Redis Pub/Sub fan-out  •  ConnectionManager",    VIOLET),
    ("AI Workers",      "11 × independent Docker services  •  Redis Streams XCONSUME  •  GPU/CPU auto-detect", ACCENT_AMBER),
    ("Data Store",      "PostgreSQL 16 + RLS  •  pgvector embeddings  •  pg_partman monthly partitions", TEAL),
    ("Message Bus",     "Redis 7  •  Streams (frame_jobs)  •  Pub/Sub (tenant_events)",          VIOLET),
    ("Observability",   "Prometheus scrape  •  Grafana dashboards  •  Structured logging",       TEAL),
    ("Compliance",      "Audit log chain verify  •  PDPA/DSAR  •  Evidence SHA-256 checksums",   ACCENT_GREEN),
]

for i, (layer, detail, color) in enumerate(layers):
    y = 1.15 + i * 0.73
    panel = s.shapes.add_shape(1, Inches(0.3), Inches(y), Inches(12.7), Inches(0.62))
    panel.fill.solid(); panel.fill.fore_color.rgb = BG_PANEL; panel.line.fill.background()

    # Colored left tab
    tab = s.shapes.add_shape(1, Inches(0.3), Inches(y), Inches(0.18), Inches(0.62))
    tab.fill.solid(); tab.fill.fore_color.rgb = color; tab.line.fill.background()

    txt(s, layer, 0.58, y + 0.05, 2.0, 0.35, size=12, bold=True, color=color)
    txt(s, detail, 2.6, y + 0.1, 10.3, 0.42, size=11, color=LIGHT_GRAY)

txt(s, "Enterprise AI Video Surveillance — 7th AI Vision  |  Confidential",
    0.3, 7.15, 12.5, 0.3, size=8, color=MID_GRAY)


# ════════════════════════════════════════════════════════════════════════════
# SLIDE 19 — Deployment & Security
# ════════════════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
bg(s)
shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
shape.fill.solid(); shape.fill.fore_color.rgb = ACCENT_GREEN; shape.line.fill.background()

hdr = s.shapes.add_shape(1, Inches(0.12), Inches(0), Inches(13.21), Inches(1.05))
hdr.fill.solid(); hdr.fill.fore_color.rgb = BG_PANEL; hdr.line.fill.background()
txt(s, "Deployment & Security Posture", 0.3, 0.05, 9.0, 0.55, size=26, bold=True, color=WHITE)
txt(s, "On-premise or cloud — built for 7–10 year operational lifespan", 0.3, 0.62, 10.0, 0.38, size=13, color=ACCENT_GREEN)

col1 = [
    ("Deployment",   ["Docker Compose (dev/on-prem)", "Kubernetes-ready (Helm charts path)", "GPU passthrough via NVIDIA Container Toolkit", "Windows native Electron .exe"]),
    ("Database",     ["PostgreSQL 16 with Row-Level Security", "Monthly table partitioning (pg_partman)", "pgvector for 512-d face embeddings", "7-year audit log archival — never dropped"]),
    ("Auth & Access",["JWT with per-kid signing key rotation", "TOTP two-factor authentication", "Per-account lockout (configurable attempts)", "IP allowlist per tenant", "Active session revocation"]),
]
col2 = [
    ("API Security", ["SlowAPI rate limiting (Redis-backed)", "5/min on login (brute-force protection)", "CORS + CSP + X-Frame-Options headers", "API key scoping for programmatic access"]),
    ("AI Pipeline",  ["Redis Streams crash recovery (XCLAIM)", "Model version tracked per detection row", "Tenant-configurable thresholds (no redeploy)", "30-second TTL settings cache in workers"]),
    ("Compliance",   ["PDPA-ready: DSAR management, masking", "SHA-256 evidence checksums", "Blockchain-style audit chain verification", "Semantic-versioned releases + rollback runbook"]),
]

for ci, col in enumerate([col1, col2]):
    lx = 0.3 + ci * 6.5
    ty = 1.15
    for section, items in col:
        txt(s, section, lx, ty, 6.0, 0.35, size=13, bold=True, color=TEAL)
        divider(s, lx, ty + 0.35, 5.8, TEAL, 0.02)
        for item in items:
            txt(s, f"  ✓  {item}", lx + 0.1, ty + 0.4, 5.8, 0.32, size=11, color=LIGHT_GRAY)
            ty += 0.32
        ty += 0.5

txt(s, "Enterprise AI Video Surveillance — 7th AI Vision  |  Confidential",
    0.3, 7.15, 12.5, 0.3, size=8, color=MID_GRAY)


# ════════════════════════════════════════════════════════════════════════════
# SLIDE 20 — Closing / Thank You
# ════════════════════════════════════════════════════════════════════════════
s = prs.slides.add_slide(BLANK)
bg(s)
gradient_box(s, 0, 0, 13.33, 7.5, BG_DARK, BG_PANEL)
shape = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.12), Inches(7.5))
shape.fill.solid(); shape.fill.fore_color.rgb = VIOLET; shape.line.fill.background()

# Large decorative circles
for cx, cy, sz, col in [(9.5, 1.5, 3.5, VIOLET), (10.5, 3.0, 2.5, TEAL), (8.5, 4.0, 1.5, VIOLET)]:
    c = s.shapes.add_shape(9, Inches(cx), Inches(cy), Inches(sz), Inches(sz))
    c.fill.solid(); c.fill.fore_color.rgb = RGBColor(0x18, 0x10, 0x40)
    c.line.color.rgb = col; c.line.width = Pt(1)

txt(s, "7th AI Vision", 0.5, 1.5, 9.0, 1.0, size=52, bold=True, color=WHITE)
txt(s, "Where AI Meets Physical Security", 0.5, 2.55, 9.0, 0.65, size=24, color=TEAL)
divider(s, 0.5, 3.35, 7.0, VIOLET)

txt(s, "Built for enterprise. Proven in production. Ready to deploy.", 0.5, 3.55, 9.0, 0.5, size=16, color=LIGHT_GRAY)

summary_points = [
    "11 AI detection modules — all running simultaneously per camera",
    "104 / 104 automated backend tests passing",
    "Multi-tenant SaaS with PostgreSQL Row-Level Security",
    "Real-time WebSocket push — zero-polling alert delivery",
    "Native Windows desktop app + iOS/Android mobile app",
    "Full PDPA compliance with DSAR and privacy masking tools",
]
bullet_list(s, summary_points, 0.5, 4.15, 8.5, 2.5, size=13, color=LIGHT_GRAY, bullet="◆")

txt(s, "Contact: sathishtvg@gmail.com", 0.5, 6.7, 5.0, 0.4, size=12, bold=True, color=TEAL)
txt(s, "© 2026 Seventh AI Vision. All rights reserved.", 0.5, 7.1, 10.0, 0.35, size=9, color=MID_GRAY, italic=True)


# ════════════════════════════════════════════════════════════════════════════
# Save
# ════════════════════════════════════════════════════════════════════════════
prs.save(OUT)
print(f"Saved: {OUT}")
print(f"Slides: {len(prs.slides)}")
