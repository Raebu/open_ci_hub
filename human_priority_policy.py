"""Foreground-first resource allocation for a trusted telemetry stream.

Unknown desktop activity is busy, never consent to start background work.
"""
from __future__ import annotations

def throttle(*,seconds_since_input,available_mb,temperature_c,call_active=False,
             gpu_busy=False,required_mb=512):
    if (seconds_since_input is None or available_mb is None
        or temperature_c is None or type(available_mb) is not int
        or type(required_mb) is not int):
        return {"mode":"paused","reason":"missing_user_telemetry"}
    if call_active:
        return {"mode":"paused","reason":"foreground_call"}
    if temperature_c>=85:
        return {"mode":"paused","reason":"thermal_pressure"}
    if available_mb<max(4096,required_mb+3072):
        return {"mode":"paused","reason":"memory_pressure"}
    if seconds_since_input<15:
        return {"mode":"paused","reason":"recent_human_input"}
    if temperature_c>=72 or seconds_since_input<90 or gpu_busy:
        return {"mode":"quiet","cpu_quota_percent":15,"reason":"protecting_interaction"}
    return {"mode":"background","cpu_quota_percent":45,"reason":"confirmed_idle_capacity"}
