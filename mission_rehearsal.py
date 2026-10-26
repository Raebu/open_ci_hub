"""Static, side-effect-free mission rehearsal evidence.

No browser network calls and no action execution. The owner reviews this
report before separately authorising a mission.
"""
from __future__ import annotations
import hashlib
import json
from autonomy_core import canonical,validate_plan

def rehearse(plan):
    checked=validate_plan(plan)
    steps=[]
    for step in checked["steps"]:
        kind=step["kind"]
        interactive=(kind=="browse" and any(a["type"] in ("click","fill") for a in step["input"]["actions"]))
        steps.append({"id":step["id"],"kind":kind,"depends_on":list(step["needs"]),
            "interaction":interactive,"approved_hosts":list(checked["hosts"]),
            "execution":"not_started"})
    return {"schema":"open-ci-hub.rehearsal.v1",
        "mission_id":checked["id"],"plan_sha256":hashlib.sha256(canonical(checked).encode()).hexdigest(),
        "steps":steps,"executed":False,
        "notice":"Policy rehearsal only; no actual website actions or costs were checked."}
