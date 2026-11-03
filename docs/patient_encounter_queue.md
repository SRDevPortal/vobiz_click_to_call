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
When NDR is enabled in Vobiz Settings, NDR is also available in this picker.
The console source switcher offers the saved selections. Existing single-source
and CRM Lead and Patient mappings are recognized without a bulk data update.
Selecting Patient still requires its department and follow-up routing settings.
The Patient Encounter List tab retains its configured columns and filters.

## NDR tab

In **Vobiz Settings → NDR Queue**, choose **Shipkia Stages** and **Shipkia
Statuses** using the searchable multiselects, check **Enable NDR Tab**, and Save.
NDR starts disabled; both selections must contain at least one value to enable it.
Then open each intended agent's **Vobiz User Mapping**, select **NDR** in
**Queue Source**, and save. Only users with an enabled mapping containing NDR
and Patient Encounter read permission can see the tab or use its list/Details
APIs. Administrators also need an explicit NDR mapping. Existing encounter and
Patient workdesk permissions still apply; mapping NDR does not grant record access.

Disabling NDR globally hides the option and tab and blocks NDR API access.
Existing NDR assignments are retained for re-enabling; editing other queue sources
while NDR is disabled preserves that assignment. New assignments are rejected
until NDR is enabled again. To remove an assignment using the picker, enable NDR
and deselect it. Users can have NDR alone or alongside other queue sources. NDR
does not enter the main lead/auto-dial queue or grant access to CRM leads.
Choices come from existing, readable Patient Encounter shipment values. Previously
selected values remain available even when no current encounters use them.

The third Agent Console tab, **NDR**, shows encounters matching the saved Encounter
Queue filters **AND** any selected stage **AND** any selected status. Conflicting
inherited filters can result in an empty list. NDR can be enabled independently of
the Patient Encounter List. It uses the same document and field permissions.

Shipkia Stage (`pe_shipkia_stage`) and Shipkia Status (`pe_shipkia_status`) occupy
columns 3 and 4, after the first two other configured columns. For configurations
with fewer than two other columns, Encounter ID/Patient supply the leading columns.
Remaining columns keep their order, without duplicate Shipkia columns; field
permissions still apply. Click either Shipkia header to sort ascending/descending
across all matches. The initial order remains most recently updated first.

Search, 25-record pagination, refresh and Details work like the Encounter list.
Each tab retains its own search, page and sort state while switching tabs. Details
rechecks the saved filters and current shipment values before opening the same
Patient workdesk and calling controls. Opening Details does not start a call.
NDR does not add encounters to auto-dial. Refresh the console after changing rules;
returning from Settings updates tab visibility.

Existing sites need a targeted settings schema reload and cache clear:

```sh
bench --site <site> reload-doc vobiz_click_to_call doctype vobiz_settings
bench --site <site> clear-cache
```

Focused validation:

```sh
env/bin/python -m unittest vobiz_click_to_call.tests.test_encounter_queue -q
node apps/vobiz_click_to_call/vobiz_click_to_call/tests/test_encounter_queue.cjs
```
