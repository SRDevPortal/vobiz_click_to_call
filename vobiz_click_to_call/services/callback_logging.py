"""Worker adapter for the shared concurrency-safe callback history writer."""


def append_callback_job(call_log, event_type, payload):
    # event is reserved by frappe.enqueue; keep event_type at the job boundary.
    append_callback(call_log=call_log, event=event_type, payload=payload)


def append_callback(call_log: str, event: str, payload: dict) -> None:
    from vobiz_ai.api.call_log import append_callback as append_history

    append_history(call_log=call_log, event=event, payload=payload)
