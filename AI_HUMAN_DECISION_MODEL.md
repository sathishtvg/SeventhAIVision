# AI Security Intelligence — The Human Decision Model

**As of:** 2026-10-05 · phase 7 of 15 · migration `0137` ·
`backend/app/services/intel_decisions.py`, `intel_actions.py`,
`backend/app/routers/security_decisions.py`

> AI detects, understands, correlates, assesses and recommends. Authorised
> human security personnel make the final security decision.

This document is about the second sentence: who may decide, how far, what an
override is, and what is kept as the record. How a decision is then carried out
is in `AI_DECISION_WORKFLOW.md`.

## What cannot happen

Each of these is held by a test, not by intention.

- **The layer never decides.** There is no default decision, no decision on a
  timer, and no path from the background runner to the code that decides or
  acts. The runner's imports are followed to their end and never arrive there.
- **Nothing is carried out without a person's decision**, and where the policy
  asks for a second person, without their approval.
- **A decision is not accepted from an API key**, nor from the vendor's support
  session. It is made by a person of the organisation, signed in.
- **A suggestion is never shown as a decision**, and a decision is never shown
  as something done. They are three tables.
- **A decision is never rewritten.** The application's database role may add
  and read decisions, approvals, actions and reviews. It can neither change nor
  remove one.
- **An incident the platform opened by itself is never shown as one a person
  stands behind.**

## What it takes to decide

All five, or the decision is refused and nothing is recorded as decided.

| | Needs | Refused with |
|---|---|---|
| 1 | To be a person, signed in | 403 |
| 2 | The permission `intel:decide` | 403 |
| 3 | The situation's site, for someone restricted to certain sites | 404, as if it did not exist |
| 4 | The authority the **decision policy** gives that person's role at this risk | 403, with the policy's own sentence |
| 5 | To be carried out alone: the platform's own permission for each step — `incident:dispatch` to dispatch, `incident:resolve` to resolve an incident, and so on | 403, naming the permission |

`intel:decide` by itself carries nothing out. And going against what was
suggested needs `intel:override` as well.

## The decision policy

Who may decide is configuration, not code. A policy says, for each role, the
highest risk it may decide **alone**, and the highest it may decide **with
approval** — recorded, but carried out only when a second person approves.
A role with neither may not decide.

```json
{"roles": {"5": {"alone": "MEDIUM", "with_approval": "CRITICAL"}}}
```

Roles a policy can speak about are the ones that can hold `intel:decide`:
admin (`2`), manager (`8`), supervisor (`3`), operator (`4`), guard (`5`). A
role the policy does not mention keeps the default.

**The default**, when an organisation has set nothing — the command centre
decides, a guard does not:

```json
{"2": {"alone": "CRITICAL"}, "8": {"alone": "CRITICAL"}, "3": {"alone": "CRITICAL"}, "4": {"alone": "CRITICAL"}, "5": {}}
```

**The three policies of the specification are each a setting**, not a mode:

| | Policy | `roles` |
|---|---|---|
| A | The command centre controls incidents | `{"5": {}}` |
| B | A site guard handles low and medium risk | `{"5": {"alone": "MEDIUM"}}` |
| C | High risk needs the command centre's approval | `{"5": {"alone": "MEDIUM", "with_approval": "CRITICAL"}}` |

Nothing limits it to those three: the command centre's own roles can be held
back too, for instance an operator alone only up to `HIGH`.

- **Per site.** A site can have its own policy; it then stands in place of the
  organisation's for that site, as a whole.
- **It only narrows.** A policy cannot let a role decide that lacks
  `intel:decide`, nor carry out a step the role's permissions do not allow.
- **Not yet assessed is the highest risk.** A situation the layer has not
  assessed asks for the most authority, not the least.
- **Asking for help is always open.** `REQUEST_ASSISTANCE` is handing the
  decision up, not taking it: anyone who may decide at all may ask, whatever
  the policy says.
- **Changing a policy** needs `intel:manage` and is audited.

## Approval by a second person

When the policy lets a role decide at this risk only with approval, the
decision is recorded as that person's, the situation is marked
`PENDING_APPROVAL`, and **nothing is carried out**.

The approver must hold `intel:approve`; must be **someone other than the person
who decided**; must have the authority to take that decision **alone** — at the
risk it was made at, or the risk now if that is higher; and must hold the
platform's permission for each step, because it is carried out under theirs.

- **Approved:** the steps are carried out, and each is recorded as executed by
  the approver. The decision remains the first person's; the approval is its
  own record beside it.
- **Rejected:** a note saying why is required. Nothing is carried out and the
  situation is back to `AWAITING`.
- **Overtaken:** if someone with the authority decides the situation directly
  while a decision waits, the waiting one can no longer be approved.

A decision has one verdict, once.

## Override

Every decision has a basis, worked out by the layer and never chosen by the
caller:

| Basis | When |
|---|---|
| `FOLLOWED` | The step is one the layer suggested and listed as possible — any of them, not only the first |
| `OVERRIDE` | The step was not suggested, or was listed as not possible |
| `INDEPENDENT` | Neither for nor against: acknowledging, asking for help, confirming an incident, or deciding before anything had been suggested |
| `CLOSING` | `FALSE_POSITIVE` or `RESOLVE`: the matter is ended |

**An override is never blocked.** Someone who holds `intel:override` may choose
any step whatever was suggested — including sending a guard when the layer
said nobody was on shift. What is asked for is a reason:

| Code | Reason |
|---|---|
| `AUTHORISED_ACTIVITY` | Authorised activity |
| `ALREADY_HANDLED` | Already handled |
| `FALSE_DETECTION` | False detection |
| `GUARD_RESPONDING` | Guard already responding |
| `MAINTENANCE` | Maintenance activity |
| `EMERGENCY` | Emergency situation |
| `CAMERA_ISSUE` | Camera issue |
| `OTHER` | Other — with a note saying what |

A reason is required for an `OVERRIDE` and for a `CLOSING` decision. Both the
API and the database refuse one without.

## The records

| Table | Holds | Written by |
|---|---|---|
| `security_recommendations` | What the layer suggested (phase 6) | The runner |
| `security_reviews` | That a person looked at what was suggested: who, in what role, which assessment, when. Once per person per assessment | The API, when an officer opens it |
| `security_decisions` | What a person decided: who, role, the step, the basis, the reason and note, the assessment it was made on and the one that was on their screen, the risk then, what was suggested first, how the policy let them decide, and the policy's own sentence | The API, on that person's request |
| `security_decision_approvals` | A second person's verdict on a decision that needed one | The API, on the approver's request |
| `security_actions` | What the platform then did: the step, the existing function it went through, the target, how it ended, and under whose authority | The API, after the decision is saved |
| `security_decision_policies` | Who may decide: one for the organisation, at most one per site | An administrator |

The first five are added to and read; the application's role is granted
`SELECT` and `INSERT` on them and nothing else. All six are tenant-scoped under
forced row-level security.

A decision records `decided_on_an_earlier_assessment` when a newer assessment
existed than the one on the officer's screen. It is not refused for that: an
officer under pressure is not made to read the screen again, and the authority
is always judged against the latest.

## Where a situation stands

`decision_status` on the situation is set only by a person's decision, never by
the layer.

| Status | Means |
|---|---|
| `AWAITING` | Nobody has decided anything; or a proposed decision was rejected |
| `ACKNOWLEDGED` | Someone has seen it |
| `IN_HAND` | Someone has decided on a step |
| `PENDING_APPROVAL` | A decision waits for a second person |
| `ASSISTANCE_REQUESTED` | Someone has asked the command centre for help |
| `RESOLVED` | A person closed it as dealt with |
| `FALSE_POSITIVE` | A person closed it as not real |

A closed situation takes no further decision and no further event: what the
same camera reports afterwards is a new situation.

## Incidents: three things that are not the same

| State | Means |
|---|---|
| `NONE` | An AI event and nothing more. No incident exists |
| `PRELIMINARY` | The platform opened an incident by itself — it does so for a guard's SOS, for a verified drone event and in a few other cases — and no person has confirmed it |
| `CONFIRMED` | A person opened the incident, or a person confirmed it with `CONFIRM_INCIDENT` |

Confirming is this layer's record. The incident itself is not altered: the
`incidents` table is the platform's and is left as it is.

## Audit

Written to the tenant's existing hash-chained audit log, each with the actor,
their role, the site, the source, the request id and the result:

| Entry | When |
|---|---|
| `intel.recommendation.view` | A person opens what was suggested, the first time for each assessment |
| `intel.decision.record` | A decision — with the step, the basis, whether it is an override, the reason code, what was suggested first, the assessment, the risk and how the policy let it be made |
| `intel.decision.approve`, `intel.decision.reject` | A second person's verdict |
| `intel.action.<step>` | Each step carried out — guard dispatched, escalation, incident opened or resolved — with the function it went through and how it ended |
| `intel.decision_policy.update`, `intel.decision_policy.delete` | A change to who may decide |

**Assessments and recommendations being generated** are not entries in that
log. The record of each is the row itself, which the application can add but
not alter, and every decision's entry names the assessment and the suggestion
it was made on. The runner writes only the layer's own tables, and writing to
the audit log from it would have meant giving that up.

## Limits

- **A role, not a person, is what the policy speaks about.** Two supervisors
  have the same authority. A particular officer cannot be given more or less
  than their role.
- **On shift is not checked for the decider.** Anyone with the authority may
  decide, on duty or not.
- **A guard without assigned sites may decide at any site**, the same as every
  other site-scoped screen for a user with no assignments. Assign guards to
  their sites to confine them.
- **Approval is not routed.** A waiting decision appears in the queue of
  everyone who could approve it; nobody in particular is asked.
- **The decider chooses the guard and the supervisor.** The layer validates
  that they exist, are active, and for an escalation hold a senior role. It
  does not choose them.
- **Nothing is undone.** A decision cannot be withdrawn. What it set off is
  reversed, if at all, on the platform's own screens.

## Tests

`backend/tests/test_intel_decisions.py` (39): the default policy and the three
of the specification; a nonsense policy refused; what kind of decision each
choice is; every reason a decision is refused, in order; followed, overridden,
closed, through the API, each step carried out by the platform's own function;
an override accepted with a reason and refused without; a preliminary incident
confirmed without touching it; a decision before anything was suggested; who
may not decide, and that a refusal records nothing; a guard within a policy; a
decision that waits, approved by a second person and carried out under their
authority; rejected with a reason; not approved by its own author, nor by
someone who could not have decided it; the audit entries; the policy API; the
schema's own refusals; and that no role of the application can change a
decision once it is recorded.
