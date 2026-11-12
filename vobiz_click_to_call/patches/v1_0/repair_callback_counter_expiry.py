"""Repair existing Vobiz callback counters without changing their counts."""
from vobiz_click_to_call.services.callback_limits import repair_callback_counter_expiry


def execute():
    return repair_callback_counter_expiry()
