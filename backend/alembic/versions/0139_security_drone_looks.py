"""AI security intelligence, phase 10: a drone look a person asked for, and what came back.

Additive, and to the layer's own tables only: three lists of allowed values
are made longer and one index is added. No table of the drone module, and
nothing else that existed before the layer, is altered.

  officer decides VERIFY_WITH_DRONE ─► the drone module's own function
        (hold the flight that saw it, or start a mission the site already has)
     ─► the drone looks ─► what it saw is an event ─► the situation is
        assessed again ─► the officer decides

WHAT IS ADDED TO `security_actions`. Two things the platform can now be asked
to do for a decision, each through a function the drone module already has:

  DRONE_HOLD    hold a flight in the air where it is and look again
  DRONE_LAUNCH  start a mission that already exists at the site

and the four kinds of thing such a step can point at: the sighting, the look
that was asked for, the mission, the flight.

WHAT IS ADDED TO `security_situation_events`. One more reason an event can be
in a situation: DRONE_LOOK — it is what came back from a look a person asked
for about this very situation.

DOWNGRADE keeps what was written. The shorter lists are put back for rows
written from then on (NOT VALID): a step that was carried out stays on the
record as it was carried out.

Revision ID: 0139
Revises: 0138
"""
from alembic import op

revision = "0139"
down_revision = "0138"
branch_labels = None
depends_on = None

ACTIONS_BEFORE = ("'ALERT_ACKNOWLEDGE','ALERT_FALSE_POSITIVE','ALERT_DISMISS','ALERT_ASSIGN','INCIDENT_CREATE',"
                  "'INCIDENT_CONFIRM','INCIDENT_DISPATCH','INCIDENT_ASSIGN','INCIDENT_RESOLVE','NONE'")
ACTIONS = ACTIONS_BEFORE + ",'DRONE_HOLD','DRONE_LAUNCH'"
TARGETS_BEFORE = "'alert','incident'"
TARGETS = TARGETS_BEFORE + ",'drone_event','drone_look','drone_mission','drone_flight'"
METHODS_BEFORE = ("'FIRST_EVENT','SAME_ALERT','DRONE_CCTV','SAME_IDENTITY','SAME_SOURCE_REPEAT',"
                  "'ACCESS_AT_CAMERA','ALARM_AT_CAMERA','ADJACENT_CAMERA','NEAR_POSITION','PATROL_FINDING',"
                  "'GUARD_SOS_AT_SITE'")
METHODS = METHODS_BEFORE + ",'DRONE_LOOK'"


def _lists(actions: str, targets: str, methods: str, *, validate: bool) -> None:
    tail = "" if validate else " NOT VALID"
    op.execute("ALTER TABLE security_actions DROP CONSTRAINT IF EXISTS ck_secaction_action")
    op.execute(f"ALTER TABLE security_actions ADD CONSTRAINT ck_secaction_action CHECK (action IN ({actions})){tail}")
    op.execute("ALTER TABLE security_actions DROP CONSTRAINT IF EXISTS ck_secaction_target")
    op.execute("ALTER TABLE security_actions ADD CONSTRAINT ck_secaction_target "
               f"CHECK (target_type IS NULL OR target_type IN ({targets})){tail}")
    op.execute("ALTER TABLE security_situation_events DROP CONSTRAINT IF EXISTS ck_secsitev_method")
    op.execute("ALTER TABLE security_situation_events ADD CONSTRAINT ck_secsitev_method "
               f"CHECK (method IN ({methods})){tail}")


def upgrade() -> None:
    _lists(ACTIONS, TARGETS, METHODS, validate=True)
    # Which situation a flight was launched for, asked once for every drone
    # event that arrives.
    op.execute("CREATE INDEX idx_secaction_target ON security_actions (target_id) WHERE target_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_secaction_target")
    _lists(ACTIONS_BEFORE, TARGETS_BEFORE, METHODS_BEFORE, validate=False)
