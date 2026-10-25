"""Read-only, secret-free deployment dashboard inventory."""
from __future__ import annotations
from pathlib import Path
import json
from deployment_engine import POLICY, STATE, load_policy, history

def inventory(policy:Path=POLICY,state:Path=STATE):
    if not Path(policy).exists():
        return {"targets":[],"configured":False,"note":"No approved deployment targets configured"}
    targets=load_policy(policy)
    items=[]
    for name,cfg in sorted(targets.items()):
        recent=history(state,name)[-10:]
        successes=[r for r in recent if r["phase"]=="healthy"]
        failures=[r for r in recent if r["phase"]=="failed"]
        items.append({
            "target":name,"application":cfg["application"],"environment":cfg["environment"],
            "adapter":cfg["adapter"],"enabled":cfg.get("enabled") is True,
            "automatic_production":cfg["environment"]=="production" and
                cfg.get("automatic") is True and cfg.get("approved_policy") is True,
            "current_commit":successes[-1]["sha"] if successes else None,
            "current_digest":successes[-1]["digest"] if successes else None,
            "last_status":recent[-1]["phase"] if recent else "not_deployed",
            "last_event":recent[-1]["when"] if recent else None,
            "previous_release":successes[-2]["digest"] if len(successes)>1 else None,
            "recent_history":[{"phase":x["phase"],"sha":x["sha"],"digest":x["digest"],
                    "when":x["when"],"record_hash":x["record_hash"]} for x in recent],
            "recent_failure":failures[-1]["when"] if failures else None,
            "rollback_control":"Disabled: owner CLI approval and live target verification required"
        })
    return {"targets":items,"configured":True,
            "note":"Local audit history. Does not itself confirm live provider state or production GO."}
