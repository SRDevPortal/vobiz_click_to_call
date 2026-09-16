# Valid status while awaiting provider confirmation

A provider start timeout now retains the supported `Queued` status and records
`provider-confirmation-pending` separately in `call_status`. It does not redial,
invent a final outcome, or release the agent solely because time has elapsed.
A callback that already advanced the call wins over a late timeout response.

The before-validation hook and migration normalize historical `Confirmation
Pending` and `Provider Unconfirmed` rows. They preserve cancellation intent and
do not fabricate end times or overwrite current agent mappings. Status reads
also repair an old row under a lock, preserving concurrent completion updates.

Deploy this revision, run `bench --site SITE migrate`, build the app, clear the
site cache, and restart web and queue workers using the hosting workflow.
Afterward, disable the temporary Bharat Server Script **Vobiz Preserve Pending
Provider Confirmation**. Keep that script enabled until deployment is complete.

This fixes the invalid-status application error. It does not resolve the network
or provider timeout itself, and an unconfirmed call can remain pending until
provider evidence arrives. Separate End Call retry changes are not part of this
commit.
