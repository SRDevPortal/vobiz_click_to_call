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
