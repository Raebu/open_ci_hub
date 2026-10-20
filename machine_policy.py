"""Conservative, dependency-free laptop pressure and thermal admission rules.

Read-only Linux interfaces; no CPU frequency or thermal-policy writes.
Missing optional thermal/battery sensors are reported honestly, not fabricated.
"""
from __future__ import annotations
import os
from pathlib import Path
import re

MODES=('quiet','balanced','performance')

def linux_pressure():
    try:
        text=Path('/proc/pressure/memory').read_text(encoding='ascii')
        line=next(x for x in text.splitlines() if x.startswith('some '))
        value=re.search(r'\bavg10=(\d+(?:\.\d+)?)\b',line)
        return float(value.group(1)) if value else None
    except (OSError,StopIteration,ValueError):
        return None

def package_temperature_c():
    """Use package/core thermal probes, never unrelated SSD/Wi-Fi thermal zones."""
    readings=[]
    try:
        for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone[0-9]*')):
            kind=(zone/'type').read_text(encoding='ascii').strip().lower()
            if kind not in ('x86_pkg_temp','cpu-thermal','cpu_thermal','soc_thermal',
                            'cpu_package','coretemp'):
                continue
            raw=(zone/'temp').read_text(encoding='ascii').strip()
            if not re.fullmatch(r'-?\d+',raw):continue
            measured=int(raw)/1000
            if 0<=measured<=125:readings.append(measured)
    except (OSError,ValueError):
        return None
    return max(readings) if readings else None

def battery_state():
    try:
        for battery in sorted(Path('/sys/class/power_supply').glob('BAT[0-9]*')):
            percent=int((battery/'capacity').read_text(encoding='ascii').strip())
            state=(battery/'status').read_text(encoding='ascii').strip().lower()
            if 0<=percent<=100:
                return {'percent':percent,'discharging':state=='discharging'}
    except (OSError,ValueError):
        return None
    return None

def system_load():
    try:
        cpus=os.cpu_count()
        return max(0,os.getloadavg()[0]/cpus) if cpus else None
    except (OSError,ValueError,ZeroDivisionError):
        return None

def sample():
    return {'temperature_c':package_temperature_c(),
            'memory_pressure_pct':linux_pressure(),
            'load_per_cpu':system_load(),
            'battery':battery_state()}

def admission(mode,telemetry,base_limit):
    if mode not in MODES:raise ValueError('Invalid operating mode.')
    if type(base_limit)!=int or not 1<=base_limit<=32:
        raise ValueError('Invalid parallelism budget.')
    data=telemetry if isinstance(telemetry,dict) else {}
    temperature=data.get('temperature_c')
    pressure=data.get('memory_pressure_pct')
    load=data.get('load_per_cpu')
    battery=data.get('battery')
    ceiling=1 if mode=='quiet' else base_limit
    reason='normal'
    # Performance mode may use the owner's configured base ceiling, not override it.
    if isinstance(temperature,(int,float)) and temperature>=90:
        return {'max_parallel':0,'reason':'critical_thermal','temperature_c':temperature}
    if isinstance(pressure,(int,float)) and pressure>=25:
        return {'max_parallel':0,'reason':'critical_memory_pressure','memory_pressure_pct':pressure}
    if isinstance(temperature,(int,float)) and temperature>=82:
        ceiling=min(ceiling,1);reason='thermal_throttle'
    if isinstance(pressure,(int,float)) and pressure>=10:
        ceiling=min(ceiling,1);reason='memory_pressure_throttle'
    if isinstance(load,(int,float)) and load>=1.75:
        ceiling=min(ceiling,1);reason='cpu_saturation'
    if isinstance(battery,dict) and battery.get('discharging') and type(battery.get('percent'))==int:
        if battery['percent']<=10:
            return {'max_parallel':0,'reason':'low_battery'}
        if battery['percent']<=25:
            ceiling=min(ceiling,1);reason='battery_saver'
    if mode=='quiet':
        reason='quiet_mode' if reason=='normal' else reason
    return {'max_parallel':ceiling,'reason':reason,
            'temperature_c':temperature,'memory_pressure_pct':pressure,
            'load_per_cpu':load,'battery':battery}
