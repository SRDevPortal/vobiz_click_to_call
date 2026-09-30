# Patient Encounter Queue

Open /app/vobiz-agent-console and click **Patient Encounter List**. The list has
25 records per page, search by encounter/patient ID or patient name, and a Details
button. Details opens the linked Patient workdesk, identifies the selected
encounter, and uses the existing calling and number-choice controls. Opening
Details does not place a call. Calls remain linked to the Patient.

System Managers configure the list in **Vobiz Settings ? Configure Encounter
Queue**. Select up to 20 columns and add filters; all filters must match.
Apply to Settings, then Save. The JSON fields also allow explicit column ordering.
Refresh the encounter list after changing settings.

Example filter: **Shipkia Status != Delivered**. This includes blank shipment
statuses. Add **Shipkia Order ID is set** to limit the queue to shipped orders.
Other conditions, such as Payment Status and encounter dates, can be combined.

The queue uses normal Patient Encounter read permissions and field permissions.
Details rechecks encounter visibility and the existing Patient workdesk access.
The settings do not grant document access or add encounters to auto-dial.

The encounter list UI is maintained in both the base click-to-call console and
the browser-call console. On sriaas.local, Vobiz System Call owns the Page.
New installations/migrations synchronize the added Vobiz Settings fields.
For an existing site, reload the Vobiz Settings DocType and clear cache.

Validation:
- env/bin/python -m unittest vobiz_click_to_call.tests.test_encounter_queue vobiz_click_to_call.tests.test_agent_console_autodial -q
- node apps/vobiz_click_to_call/vobiz_click_to_call/tests/test_encounter_queue.cjs

## User mapping sources

Queue Source in Vobiz User Mapping is a multi-select dropdown. Select any
combination of CRM Lead, Patient, Patient Encounter, Issue and Discontinued.
The console source switcher offers the saved selections. Existing single-source
and CRM Lead and Patient mappings are recognized without a bulk data update.
Selecting Patient still requires its department and follow-up routing settings.
The Patient Encounter List tab retains its configured columns and filters.
