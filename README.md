# Vobiz Click To Call

Frappe app for Vobiz.ai click-to-call. A mapped ERP user can click a phone icon on supported records, Vobiz calls the customer first, and the app returns Voice XML to dial the mapped user mobile after the customer answers.

## Current Features

- Click-to-call buttons on configured DocTypes.
- User mapping as permission source; no separate dialer user role is required.
- Agent availability: Available, Busy, Away, Offline.
- Automatic Busy status while a Vobiz call is active, with optional auto-restore after call end.
- Vobiz recording start after call connection.
- Recording and transcription callback storage on Vobiz Call Log.
- Optional AI disposition from transcript using an OpenAI-compatible Responses API key.

## Setup Notes

- Configure `Vobiz Settings` with Auth ID, Auth Token, caller ID, webhook base URL, and allowed DocTypes.
- Create one `Vobiz User Mapping` per user who can call.
- Enable recording/transcription only after confirming consent and billing requirements.
- For AI disposition, configure `openai_api_key` or `vobiz_openai_api_key` in `site_config.json`, or set the app-level key in `Vobiz Settings`.

## Customer-number privacy: console references

When Privacy Shield is installed, its site switch is enabled, and the user lacks
View Full, this phase masks queue reference phones, reference titles/previews,
lead-detail phone fields, Patient number choices, and provider click-to-call
start responses. Console call history and missed-call responses also mask
customer numbers and phone-like text in summaries, transcripts, and error
messages. These display projections preserve call IDs, agent numbers, and
stored text. Full-visibility users and disabled mode retain their existing
response contracts.

Phone choices sent to the browser are opaque HMAC identifiers bound to the
site, user, source document, and current stored number. The console frontend
submits the identifier without sending the masked display value as a destination.
The shared resolver searches only the currently available phone candidates on
the authorized record and its existing linked-customer sources. Ordinary call
permissions and safety checks still apply. The browser identifier contains no
reversible phone value. Key rotation or a changed number requires refreshing
the choice. Internal queue matching, source records, and provider numbers are
kept unchanged.

This app now also projects callback notifications and call-disconnected events
for the receiving user. Browser softphone destinations, system-dialer URLs,
incoming lookup, and conference recovery belong to the vobiz_system_call
integration and are reviewed in that app. Other workdesk data and call-log or
transcript routes still need separate review.

Validation includes projection and selection regression tests, the frontend
request contract, completion-event tests, and a read-only permitted-record
check. No real calls or messages were sent.

Restricted console appointment creation omits the apt_mobile_number default,
retaining the Patient link. The site's Patient Appointment field uses
patient.mobile as its native fetch_from; Frappe resolves the original number
during server link validation. No display mask is passed as a saved phone.
Without a linked Patient, the user must select one in the appointment form.
This only closes the console default response: standard Patient Appointment
form fetching, document responses, and other creation defaults need separate
privacy review.

Callback notifications and call-disconnected events retain original numbers
for routing and storage; their browser-facing payloads are projected for the
receiving user.
