"""The licence catalogue catches up with what the platform now has.

The vendor's catalogue of what an organisation is licensed for (migration
0022: two products, six modules each) was written before Virtual Patrolling,
AI Security Intelligence and the enterprise expansion. The platform owner could
switch on or off, for a customer, only what existed then.

This adds the modules built since, each under the product it belongs to. Rows
only: no table is created or altered.

NOTHING CHANGES FOR ANY CUSTOMER. A module with no row of the customer's own is
on whenever its product is licensed - that is how the catalogue has always
read - so every organisation has each new module exactly as it had it before
this migration, until the platform owner switches one off.

THE CATALOGUE IS THE VENDOR'S RECORD of what a customer has been sold. It does
not, today, stop a route from answering: only the AI modules' licences
(`/api/v1/licenses`) and the Drone Patrol licence do that.

DRONE PATROL IS NOT A ROW HERE. It has a licence of its own, with limits - how
many drones, missions and sites - and an expiry (`drone_module_licenses`,
migration 0123). It is switched on the Drone Patrol tab beside this catalogue.

Revision ID: 0158
Revises: 0157
"""
from alembic import op

revision = "0158"
down_revision = "0157"
branch_labels = None
depends_on = None

#: (product, code, name, what it is, icon, order)
MODULES = [
    ("seventh_ai_vision", "virtual_patrol", "Virtual Patrolling",
     "Scheduled camera-by-camera patrols from a screen, with a report", "TravelExplore", 7),
    ("seventh_ai_vision", "security_intelligence", "AI Security Intelligence",
     "Events related into situations, assessed, with a suggestion a person decides on", "Psychology", 8),
    ("seventh_ai_vision", "investigation", "Smart Investigation",
     "One search across the records, and the trail of a plate or a watchlist face", "ManageSearch", 9),
    ("seventh_ai_vision", "evidence_packages", "Evidence Packages",
     "Sealed, checksummed packages with holds and a chain of custody", "Inventory2", 10),
    ("seventh_ai_vision", "security_map", "Security Map",
     "Cameras, guards, drones, incidents and the places of a site on one map", "Map", 11),
    ("seventh_ai_vision", "risk_advice", "Risk Patterns and Advice",
     "Where what went wrong gathers, with how much history each statement rests on", "Insights", 12),
    ("seventh_ai_vision", "operations_board", "Operations Board and Briefing",
     "Every part of the operation counted together, a daily briefing and reports as files", "SpaceDashboard", 13),
    ("seventh_ai_vision", "cases", "Security Cases",
     "A matter held together: its people, tasks, linked records and names, closed by two", "FolderShared", 14),
    ("seventh_ai_vision", "data_retention", "Data Retention and Subject Reports",
     "Every retention period in one statement, and where a person appears in the records", "Policy", 15),
    ("shift_secure", "guard_response", "Guard Response and SLA",
     "The steps of a response, three clocks and escalation policies", "AvTimer", 7),
    ("shift_secure", "sop_library", "SOP Library",
     "Versioned, approved procedures, quoted beside the incident", "MenuBook", 8),
    ("shift_secure", "visitor_authorisation", "Visitor Authorisation",
     "A host's yes or no to a visit, for where and for how long, read at the gate", "HowToReg", 9),
    ("shift_secure", "assets_maintenance", "Assets and Maintenance",
     "Device health, an asset register and work orders a person accepts", "Build", 10),
    ("shift_secure", "workforce_readings", "Workforce Readings",
     "What is recorded of a guard's work, and recommendations a manager answers", "Groups", 11),
]


def upgrade() -> None:
    values = ",\n            ".join(
        f"('{product}', '{code}', '{name}', '{about.replace(chr(39), chr(39) * 2)}', '{icon}', {order})"
        for product, code, name, about, icon, order in MODULES)
    op.execute(f"""
        INSERT INTO product_modules (product_id, module_code, module_name, description, icon, sort_order) VALUES
            {values}
        ON CONFLICT (product_id, module_code) DO NOTHING
    """)


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for _, code, *_ in MODULES)
    # A customer's own switch of one of these modules goes with the module.
    op.execute(f"DELETE FROM tenant_product_modules WHERE module_code IN ({codes})")
    op.execute(f"DELETE FROM product_modules WHERE module_code IN ({codes})")
