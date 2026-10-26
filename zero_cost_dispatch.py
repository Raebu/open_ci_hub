"""Broker choice must prove every resource will cost exactly zero.

No provider billing endpoint, free-tier advertisement, or speculative
credential can substitute for a confirmed account-specific hard zero-cost cap.
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class WorkerOffer:
    name: str
    owner_approved: bool
    online: bool
    capabilities: frozenset[str]
    free_ram_mb: int
    required_ram_mb: int
    monetary_cap_pence: int | None
    watts: int
    latency_ms: int
    device_kind: str

def choose(offers, required, *, battery_critical=False):
    eligible=[]
    for worker in offers:
        if not isinstance(worker,WorkerOffer):
            continue
        if (not worker.owner_approved or not worker.online
              or worker.monetary_cap_pence!=0 or type(worker.free_ram_mb)!=int
              or not 0<=worker.required_ram_mb<worker.free_ram_mb-1024
              or not set(required)<=worker.capabilities):
            continue
        if battery_critical and worker.device_kind in ("android","laptop"):
            continue
        if worker.device_kind=="cloud" and worker.monetary_cap_pence is None:
            continue
        score=(worker.watts*1000+worker.latency_ms,
               worker.latency_ms,worker.name)
        eligible.append((score,worker))
    eligible.sort(key=lambda pair:pair[0])
    return {"selected":eligible[0][1].name if eligible else None,
            "eligible":len(eligible),"billing":"strict_zero_cost",
            "reason":"eligible_owner_worker" if eligible else "no_verified_free_worker"}
