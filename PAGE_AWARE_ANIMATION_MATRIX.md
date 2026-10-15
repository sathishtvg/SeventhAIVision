# Page-aware motion: every page, and what it has

Written by `frontend/src/motion/matrix.test.ts` from the pages themselves, and held to them by that test:
it fails when this file and the code disagree. To write it again after a page changes:
`UPDATE_MATRIX=1 npx vitest run src/motion/matrix.test.ts` (in `frontend/`).

`yes` it has it. `-` it does not apply (a page that fetches nothing has no failed state; a page with no
filtered list keeps no rows). `NO` it applies and is not there.

- 123 files under `src/pages`; 114 are a page with an address of their own, the rest are parts of one.
- 111 pages inside the shell have a scene of their own; 0 have none.
- 116 fetch; 115 say so when a request failed; 1 do not.
- 116 have a placeholder; 42 keep a list's rows while a changed filter is fetched; 8 mark what arrives.

| Page | Address | Scene | Arrives as | Says when a request failed | Placeholder | Lists that keep their rows | Marks what arrives | Its own states |
|---|---|---|---|---|---|---|---|---|
| `AccessControl.tsx` | /access | gate | table | yes | yes | 1 | - | - |
| `ActionCenter.tsx` | /action-center | ledger (never covered) | operational | yes | yes | - | yes | figures count to real values; cards take turns |
| `Alarms.tsx` | /alarms | bell (never covered) | operational | yes | yes | 1 | - | - |
| `AlertDedup.tsx` | /alert-dedup | bell | table | yes | yes | - | - | - |
| `AlertRules.tsx` | /alert-rules | bell | table | yes | yes | - | - | - |
| `Alerts.tsx` | /alerts | bell | table | yes | yes | 1 | yes | - |
| `Analytics.tsx` | /analytics | chart | dashboard | yes | yes | - | - | figures count to real values; cards take turns |
| `ApiKeys.tsx` | /api-keys | sliders | table | yes | yes | - | - | - |
| `assets/AssetsMaintenance.tsx` | /assets-maintenance | ledger | table | yes | yes | 3 | - | - |
| `Attendance.tsx` | /attendance | clock | table | yes | yes | - | - | figures count to real values; cards take turns |
| `Audit.tsx` | /audit | ledger | table | yes | yes | - | - | - |
| `Barriers.tsx` | /barriers | gate | table | yes | yes | 1 | - | figures count to real values; cards take turns |
| `board/OperationsBoard.tsx` | /operations-board | console | operational | yes | yes | 1 | - | - |
| `BWC.tsx` | /bwc | camera | operational | yes | yes | 1 | - | - |
| `Cameras.tsx` | /cameras | camera | table | yes | yes | - | - | - |
| `cases/Cases.tsx` | /cases | casefile | table | yes | yes | 1 | - | - |
| `CertificationCompliance.tsx` | /certification-compliance | badge | table | yes | yes | - | - | - |
| `ClientPortal.tsx` | (part of a page) | - | - | yes | yes | - | - | - |
| `CommandCentre.tsx` | /command-centre | console | operational | yes | yes | - | yes | figures count to real values; cards take turns |
| `Compliance.tsx` | /compliance | route | table | yes | yes | 1 | - | - |
| `Contractors.tsx` | /contractors | badge | table | yes | yes | 2 | - | - |
| `Dashboard.tsx` | / | dashboard | dashboard | yes | yes | - | - | figures count to real values; cards take turns |
| `DefectLog.tsx` | /defects | warning | table | yes | yes | 1 | - | - |
| `Detections.tsx` | /detections | search | table | yes | yes | - | - | - |
| `Developer.tsx` | /developer | sliders | table | yes | yes | - | - | - |
| `DeviceProtocols.tsx` | /device-protocols | camera | table | yes | yes | - | - | - |
| `drones/DroneAnalytics.tsx` | /drone-analytics | chart | dashboard | yes | yes | - | - | - |
| `drones/DroneDashboard.tsx` | /drones | drone | dashboard | yes | yes | - | - | - |
| `drones/DroneEvent.tsx` | /drone-events/:id | warning | detail | yes | yes | - | - | video states |
| `drones/DroneEvents.tsx` | /drone-events | warning | table | yes | yes | 1 | yes | - |
| `drones/DroneMissions.tsx` | /drone-missions | route | table | yes | yes | - | - | - |
| `drones/DroneNav.tsx` | (part of a page) | - | - | - | - | - | - | - |
| `drones/DronePatrol.tsx` | /drone-patrols/:id | drone (never covered) | detail | yes | yes | - | - | video states; says when stale |
| `drones/DronePatrols.tsx` | /drone-patrols | drone | table | yes | yes | 1 | - | - |
| `drones/DroneReportPanels.tsx` | (part of a page) | - | - | yes | yes | - | - | - |
| `drones/DroneZones.tsx` | /drone-zones | radar | table | yes | yes | - | - | - |
| `drones/MissionDesigner.tsx` | /drone-missions/:id | route | detail | yes | yes | - | - | - |
| `DutyAssignments.tsx` | /duty-assignments | badge | table | yes | yes | - | - | - |
| `EmergencyBroadcast.tsx` | /emergency | broadcast (never covered) | operational | yes | yes | - | - | - |
| `EquipmentRegister.tsx` | /equipment | ledger | table | yes | yes | 1 | - | - |
| `ErrorCentre.tsx` | /platform/errors | warning | table | yes | yes | 1 | - | - |
| `Evidence.tsx` | /evidence | film | table | yes | yes | - | - | video states |
| `evidencePackages/EvidenceHolds.tsx` | /evidence-holds | casefile | table | yes | yes | 1 | - | - |
| `evidencePackages/EvidencePackage.tsx` | /evidence-packages/:id | casefile | detail | yes | yes | - | - | - |
| `evidencePackages/EvidencePackages.tsx` | /evidence-packages | casefile | table | yes | yes | 1 | - | - |
| `Export.tsx` | /export | report | table | - | - | - | - | confirms when done |
| `fleet/FleetFields.tsx` | /gps/fields | sliders | table | yes | yes | - | - | progress as reported |
| `fleet/FleetGroups.tsx` | /gps/groups | ledger | table | yes | yes | - | - | progress as reported |
| `fleet/FleetHistory.tsx` | /gps/history | route | table | yes | yes | - | - | progress as reported |
| `fleet/FleetLiveMap.tsx` | /gps/map | radar | operational | yes | yes | - | - | - |
| `fleet/FleetProviders.tsx` | /gps/providers | broadcast | table | yes | yes | - | - | progress as reported |
| `fleet/FleetVehicle.tsx` | /gps/vehicles/:id | ledger | detail | yes | yes | - | - | progress as reported |
| `fleet/FleetVehicles.tsx` | /gps/vehicles | ledger | table | yes | yes | 1 | - | progress as reported |
| `ForgotPassword.tsx` | /forgot-password | - | - | - | - | - | - | - |
| `governance/DataRetention.tsx` | /data-retention | casefile | table | yes | yes | - | - | - |
| `GPS.tsx` | /gps | radar | operational | yes | yes | - | - | - |
| `GuardOps.tsx` | /guard-ops | dashboard | dashboard | yes | yes | - | - | - |
| `Handovers.tsx` | /handovers | clock | table | yes | yes | 1 | - | - |
| `Heatmap.tsx` | /heatmap | radar | dashboard | yes | yes | 1 | - | figures count to real values; cards take turns |
| `Incidents.tsx` | /incidents | warning | table | yes | yes | 1 | yes | - |
| `intel/Decisions.tsx` | /situation-decisions | shield | table | yes | yes | 1 | - | - |
| `intel/Feedback.tsx` | /security-feedback | shield | table | yes | yes | - | - | - |
| `intel/Insight.tsx` | /security-insight | chart | dashboard | yes | yes | - | - | - |
| `intel/IntelNav.tsx` | (part of a page) | - | - | NO | NO | - | - | - |
| `intel/IntelSetup.tsx` | /intelligence-setup | sliders | table | yes | yes | - | - | - |
| `intel/Situation.tsx` | /situations/:id | shield | detail | yes | yes | - | - | video states |
| `intel/Situations.tsx` | /situations | shield | table | yes | yes | 1 | yes | - |
| `investigations/Investigation.tsx` | /investigations/:id | casefile | detail | yes | yes | - | - | - |
| `investigations/InvestigationNav.tsx` | (part of a page) | - | - | - | - | - | - | - |
| `investigations/Investigations.tsx` | /investigations | casefile | table | yes | yes | 1 | - | - |
| `investigations/InvestigationSearch.tsx` | /investigate | search | table | yes | yes | - | - | says what it is working on |
| `Invoicing.tsx` | /invoicing | receipt | table | yes | yes | - | - | figures count to real values; cards take turns |
| `IoT.tsx` | /iot | broadcast | table | yes | yes | - | - | - |
| `IpAllowlist.tsx` | /ip-allowlist | shield | table | yes | yes | - | - | - |
| `KeyRegister.tsx` | /key-register | ledger | table | yes | yes | 1 | - | - |
| `Leave.tsx` | /leave | clock | table | yes | yes | 1 | - | figures count to real values; cards take turns |
| `LiveWall.tsx` | /live | wall | operational | yes | yes | - | - | video states |
| `Login.tsx` | /login | - | - | - | - | - | - | - |
| `LostFound.tsx` | /lost-found | ledger | table | yes | yes | 1 | - | - |
| `ManDown.tsx` | /man-down | pulse (never covered) | operational | yes | yes | 1 | yes | - |
| `MapView.tsx` | /map | radar | operational | yes | yes | - | - | - |
| `Notifications.tsx` | /notifications | bell | table | yes | yes | - | - | - |
| `occurrenceBook/BookEntries.tsx` | (part of a page) | - | - | yes | yes | 1 | - | - |
| `occurrenceBook/BookInstructions.tsx` | (part of a page) | - | - | yes | yes | 1 | - | - |
| `occurrenceBook/BookSummaries.tsx` | (part of a page) | - | - | yes | yes | - | - | - |
| `occurrenceBook/OccurrenceBook.tsx` | /occurrence-book | ledger | table | yes | yes | - | - | - |
| `Parking.tsx` | /parking | gate | table | yes | yes | 1 | - | - |
| `PatrolExecution.tsx` | /my-patrols | route | operational | yes | yes | - | - | confirms when done |
| `Payroll.tsx` | /payroll | receipt | table | yes | yes | 1 | - | figures count to real values; cards take turns |
| `PlatformAnalytics.tsx` | /platform/analytics | chart | dashboard | yes | yes | - | - | - |
| `PlatformBilling.tsx` | /platform/billing | receipt | dashboard | yes | yes | - | - | - |
| `PlatformDashboard.tsx` | /platform | dashboard | dashboard | yes | yes | - | - | figures count to real values; cards take turns |
| `PlatformInvoices.tsx` | /platform/invoices | receipt | table | yes | yes | 1 | - | - |
| `Playback.tsx` | /playback | film | operational | yes | yes | - | - | video states |
| `PostOrders.tsx` | /post-orders | ledger | table | yes | yes | 1 | - | - |
| `Recordings.tsx` | /recordings | film | operational | yes | yes | - | - | - |
| `Reports.tsx` | /reports | report | table | yes | yes | - | - | - |
| `ResetPassword.tsx` | /reset-password | - | - | - | - | - | - | - |
| `response/ResponseDesk.tsx` | /response-desk | badge (never covered) | operational | yes | yes | 1 | yes | - |
| `response/ResponseSettings.tsx` | /response-settings | sliders | table | yes | yes | - | - | - |
| `risk/RiskAdvice.tsx` | /risk-advice | chart | dashboard | yes | yes | - | - | - |
| `Roles.tsx` | /roles | badge | table | yes | yes | - | - | - |
| `Roster.tsx` | /roster | clock | table | yes | yes | - | - | cards take turns |
| `ScheduledReports.tsx` | /scheduled-reports | report | table | yes | yes | - | - | - |
| `securityMap/SecurityMap.tsx` | /security-map | radar | operational | yes | yes | - | - | - |
| `securityMap/SitePlaces.tsx` | /site-places | radar | table | yes | yes | - | - | - |
| `Settings.tsx` | /settings | sliders | table | yes | yes | - | - | - |
| `Shifts.tsx` | /shifts | clock | table | yes | yes | 1 | - | cards take turns |
| `Sites.tsx` | /sites | building | table | yes | yes | - | - | - |
| `sop/SopLibrary.tsx` | /sop-library | ledger | table | yes | yes | 1 | - | - |
| `SupportSessions.tsx` | /support-sessions | badge | table | yes | yes | - | - | - |
| `Tenants.tsx` | /tenants | building | table | yes | yes | - | - | - |
| `TenantUsage.tsx` | /platform/tenants, /platform/tenants/:tenantId | building | table | yes | yes | - | - | - |
| `Training.tsx` | /training | badge | table | yes | yes | 2 | - | - |
| `Users.tsx` | /users | badge | table | yes | yes | - | - | - |
| `Violations.tsx` | /violations | warning | table | yes | yes | 1 | - | figures count to real values; cards take turns |
| `VirtualPatrol.tsx` | /virtual-patrol | route | table | yes | yes | - | - | - |
| `visitorAuth/VisitorAuthorisations.tsx` | /visitor-authorisations | badge | table | yes | yes | 1 | - | - |
| `VisitorPreReg.tsx` | /visitor-prereg | gate | table | yes | yes | - | - | - |
| `VmsOnsite.tsx` | /vms-onsite | gate | operational | yes | yes | 1 | - | - |
| `Watchlists.tsx` | /watchlists | search | table | yes | yes | 1 | - | - |
| `workforce/WorkforceReadings.tsx` | (part of a page) | - | - | yes | yes | - | - | - |
| `Zones.tsx` | /zones | radar | table | yes | yes | - | - | - |
