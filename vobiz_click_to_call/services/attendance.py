from __future__ import annotations


# Hard-disabled to prevent high-frequency attendance scans and writes.
AGENT_ATTENDANCE_ENABLED = False


def agent_attendance_enabled() -> bool:
    return AGENT_ATTENDANCE_ENABLED
