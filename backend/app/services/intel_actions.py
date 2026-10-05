"""Actions: carrying out what a person decided, through the platform's own functions.

THE ONE MODULE IN THE LAYER THAT CHANGES ANYTHING OUTSIDE IT — and it does so
only when the API hands it a decision a person has made and, where the policy
asks for it, a second person has approved. The runner never imports it; a test
holds that.

NOTHING NEW IS DONE HERE. Every step calls the function the platform already
uses when an officer presses the existing button: acknowledging an alert,
marking it false, assigning it, opening an incident, dispatching a guard,
resolving. So a step taken from a decision is exactly the step taken by hand —
the same rows, the same events on the wire — and nothing about those functions
was changed to allow it.

EVERY STEP LEAVES A ROW, WHATEVER HAPPENED. Done, skipped because there was
nothing to do, or failed with the reason: `security_actions` says which, and
names the function it went through. A decision that calls for nothing to be
carried out leaves a row that says so.

Each existing function commits. The tenant setting ends with the transaction,
so the session is scoped again before and after every call.
"""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload
from app.routers import alerts as alerts_api
from app.routers import dispatch as dispatch_api
from app.routers import incidents as incidents_api
from app.services import intel_audit
from app.services import intel_decisions as decisions

#: What each step goes through. The right-hand side is the platform's own code.
THROUGH = {
    "ALERT_ACKNOWLEDGE": "app.routers.alerts.bulk_acknowledge_alerts",
    "ALERT_FALSE_POSITIVE": "app.routers.alerts.mark_false_positive",
    "ALERT_DISMISS": "app.routers.alerts.bulk_dismiss_alerts",
    "ALERT_ASSIGN": "app.routers.alerts.bulk_assign_alerts",
    "INCIDENT_CREATE": "app.routers.incidents.create_incident",
    "INCIDENT_DISPATCH": "app.routers.dispatch.dispatch_guard",
    "INCIDENT_ASSIGN": "app.routers.incidents.assign_incident",
    "INCIDENT_RESOLVE": "app.routers.incidents.resolve_incident",
}
#: The existing bulk functions take at most this many ids in a call.
CHUNK = 100
#: Decisions that are a record and nothing more, and what the record says.
RECORD_ONLY = {
    "VERIFY_WITH_DRONE": "Recorded. The flight is started from the drone screens: this layer does not ask "
                         "the drone module to fly.",
    "CONTACT_SITE": "Recorded. The call is made by the officer; the site's contact is on the site record.",
}
RECORDED = "Recorded. There was nothing for the platform to carry out."


def _chunks(ids: tuple) -> list[list[str]]:
    return [list(ids[i:i + CHUNK]) for i in range(0, len(ids), CHUNK)]


def incident_text(situation: Mapping, assessment: Mapping | None) -> tuple[str, str, str]:
    """(title, description, severity) for an incident a person opens from a
    situation. Made only of what is recorded, and says where it came from."""
    severity = str(situation.get("severity") or "medium")
    severity = severity if severity in ("low", "medium", "high", "critical") else "low"
    lines = [f"Opened by a person's decision from security situation {situation['situation_number']}."]
    if assessment:
        lines.append(f"AI-assisted assessment at the time: {assessment['label']}. "
                     f"Risk {assessment['risk_level']} ({assessment['risk_score']}).")
    lines.append(f"{situation.get('event_count') or 0} event(s) from: "
                 f"{', '.join(situation.get('source_types') or []) or 'not recorded'}.")
    return str(situation["title"])[:255], " ".join(lines), severity


async def carry_out(db: AsyncSession, request: Request, token: TokenPayload, *, decision: Mapping,
                    situation: Mapping, f: decisions.Facts, params: Mapping) -> list[dict]:
    """Carry out a decision that is in effect. Returns what was done, in order.
    `token` is the person under whose authority it runs: the one who decided,
    or the one who approved."""
    steps = decisions.plan(decision["action"], alerts=f.alerts, incident=f.incident)
    done: list[dict] = []
    incident_id = f.incident["id"] if f.incident is not None else None

    async def leave(action: str, through: str | None, result: str, detail: str | None,
                    target_type: str | None = None, target_id: Any = None) -> None:
        """The row for one step, and its audit entry, saved together."""
        await decisions.scope(db, token.tenant_id)
        await decisions.insert_action(
            db, decision_id=decision["id"], situation_id=situation["id"], sequence=len(done) + 1, action=action,
            through=through, target_type=target_type, target_id=target_id, result=result, detail=detail,
            user_id=token.user_id)
        await intel_audit.record(
            db, request, token, f"intel.action.{action.lower()}", "security_situation", situation["id"],
            site_id=situation.get("site_id"), result=result.lower(),
            detail={"decision_id": str(decision["id"]), "decision": decision["action"], "through": through,
                    "target_type": target_type, "target_id": str(target_id) if target_id else None,
                    "detail": detail})
        await db.commit()
        done.append({"action": action, "through": through, "result": result, "detail": detail,
                     "target_type": target_type, "target_id": target_id})

    async def call(fn, **kwargs):
        """Call one existing function. ("OK", its answer) or ("SKIPPED" |
        "FAILED", why)."""
        await decisions.scope(db, token.tenant_id)
        try:
            return "OK", await fn(db=db, **kwargs)
        except HTTPException as exc:
            await db.rollback()
            # The platform's own "nothing to do": already acknowledged, already resolved.
            return ("SKIPPED" if exc.status_code == 404 else "FAILED"), f"{exc.status_code}: {exc.detail}"
        except Exception as exc:  # noqa: BLE001 — the step failed; the next may still be worth taking
            await db.rollback()
            return "FAILED", type(exc).__name__

    for step in steps:
        through = THROUGH.get(step.action)
        if step.action == "ALERT_ACKNOWLEDGE":
            for ids in _chunks(step.ids):
                result, answer = await call(alerts_api.bulk_acknowledge_alerts,
                                            body=alerts_api.BulkIdsBody(ids=ids), token=token)
                await leave(step.action, through, result, _counted(answer, "acknowledged"), "alert")
        elif step.action == "ALERT_DISMISS":
            for ids in _chunks(step.ids):
                result, answer = await call(alerts_api.bulk_dismiss_alerts, body=alerts_api.BulkIdsBody(ids=ids))
                await leave(step.action, through, result, _counted(answer, "closed"), "alert")
        elif step.action == "ALERT_ASSIGN":
            for ids in _chunks(step.ids):
                result, answer = await call(alerts_api.bulk_assign_alerts, body=alerts_api.BulkAssignBody(
                    ids=ids, assigned_to_user_id=str(params["escalate_to_user_id"])))
                await leave(step.action, through, result, _counted(answer, "assigned"), "alert")
        elif step.action == "ALERT_FALSE_POSITIVE":
            reason = decisions.REASONS.get(decision.get("reason_code") or "", "") or None
            for alert_id in step.ids:
                result, answer = await call(alerts_api.mark_false_positive, alert_id=alert_id, token=token,
                                            body=alerts_api.FalsePositiveBody(fp_reason=reason, via=decision["via"]))
                await leave(step.action, through, result, None if result == "OK" else answer, "alert", alert_id)
        elif step.action == "INCIDENT_CREATE":
            title, description, severity = incident_text(situation, f.assessment)
            camera = situation.get("primary_camera_id")
            result, answer = await call(incidents_api.create_incident, request=request, token=token,
                                        body=incidents_api.IncidentCreate(
                                            title=title, description=description, severity=severity,
                                            camera_id=str(camera) if camera else None))
            if result == "OK":
                incident_id = answer["id"]
                # A person opened it: it is theirs, and the situation says so.
                await decisions.scope(db, token.tenant_id)
                await decisions.link_incident(db, situation["id"], incident_id)
            await leave(step.action, through, result, None if result == "OK" else answer, "incident",
                        incident_id if result == "OK" else None)
        elif step.action == "INCIDENT_CONFIRM":
            await decisions.scope(db, token.tenant_id)
            await decisions.link_incident(db, situation["id"], incident_id)
            await leave(step.action, None, "RECORDED",
                        "Confirmed by a person. The incident itself is unchanged.", "incident", incident_id)
        elif step.action == "INCIDENT_DISPATCH":
            if incident_id is None:
                await leave(step.action, through, "SKIPPED", "There is no incident to dispatch a guard to.", "incident")
                continue
            note = (decision.get("note") or "").strip() or f"From security situation {situation['situation_number']}."
            result, answer = await call(dispatch_api.dispatch_guard, incident_id=str(incident_id), token=token,
                                        body=dispatch_api.DispatchBody(guard_user_id=str(params["guard_user_id"]),
                                                                       dispatch_notes=note))
            await leave(step.action, through, result, None if result == "OK" else answer, "incident", incident_id)
        elif step.action == "INCIDENT_ASSIGN":
            result, answer = await call(incidents_api.assign_incident, incident_id=str(step.target_id),
                                        body=incidents_api.IncidentAssign(
                                            assigned_to_user_id=str(params["escalate_to_user_id"])))
            await leave(step.action, through, result, None if result == "OK" else answer, "incident", step.target_id)
        elif step.action == "INCIDENT_RESOLVE":
            result, answer = await call(incidents_api.resolve_incident, incident_id=str(step.target_id))
            await leave(step.action, through, result, None if result == "OK" else answer, "incident", step.target_id)

    if not done:
        await leave("NONE", None, "RECORDED", RECORD_ONLY.get(decision["action"], RECORDED))
    return done


def _counted(answer: Any, verb: str) -> str | None:
    """What a bulk function reported, in words; or why it did not run."""
    if isinstance(answer, dict) and "updated" in answer:
        skipped = f", {answer['skipped']} already so" if answer.get("skipped") else ""
        return f"{answer['updated']} alert(s) {verb}{skipped}."
    return answer if isinstance(answer, str) else None
