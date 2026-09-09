"""Worker adapter: event is reserved by frappe.enqueue, so use event_type."""
def append_callback_job(call_log, event_type, payload):
    from vobiz_ai.api.call_log import append_callback
    append_callback(call_log=call_log, event=event_type, payload=payload)
