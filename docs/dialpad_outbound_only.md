# Outbound-only dialpad calls

Calls started from the NDR Dialpad continue to dial normally. A customer calling
back the displayed caller-ID number is rejected with Busy when the latest
outgoing call for that exact customer and caller-ID pair has `direct_dial: true`.

The inbound router makes this decision before CRM Lead, Patient, unknown caller,
agent, or fallback routing. It returns `<Hangup reason="busy" />`, the rejection
response documented by [Vobiz](https://www.vobiz.ai/docs/xml/hangup).
The browser softphone wrapper passes this response through without upgrading it
to a SIP dial instruction.

## Examples and scope

- NDR agent uses Dialpad to call customer A from number X. A calls X back: Busy;
  no agent rings and no Pickup button appears.
- Customer B calls the shared number X: their normal routing still applies.
- Customer A calls another number Y: this policy does not borrow X's history.
- A later ordinary queue/lead/patient call to A from X restores normal callback
  handling for subsequent inbound calls. The most recent outgoing call decides.
- Existing dialpad records with the saved marker are included. Turning off the
  dialpad setting blocks new direct calls but does not erase earlier callback
  restrictions. This is not an outbound-only setting for an entire DID or agent.

## State and performance

The check uses exact normalized customer/DID filters, direction Outgoing, and
`limit=1`; it does not search request JSON with LIKE or scan phone suffixes. It
uses the existing customer/creation index. No schema change or data backfill is
introduced.

Each rejected inbound call is recorded as Busy with reason
`DIALPAD_OUTBOUND_ONLY` and a durable rejection marker. Provider retries reuse
that decision; late Ring/Hangup events do not reopen it. The router does not
reserve or release an agent mapping, publish a ringing notification, create a
lead, or route a blocked callback to another agent or AI fallback.

## Deployment and verification

Only `vobiz_click_to_call` needs this backend change; the existing dialpad and
browser wrapper need no code change. Deploy via the normal app release process.
No production setting, Server Script or provider configuration was changed by
this local implementation.

Regression coverage is in `tests/test_direct_dial_callbacks.py`. After deployment,
use a designated test customer: place a Dialpad call, end it, then call the same
caller-ID back. Confirm Busy, no agent reservation/Pickup, and the rejection
reason in the call log. Also verify a different customer's normal inbound call.
No real customer calls were made during local verification.
