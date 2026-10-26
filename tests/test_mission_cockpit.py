"""Mission cockpit shows provable progress without mission input or cookies."""
import tempfile
import unittest
from pathlib import Path
from autonomy_core import Journal
from autonomy_dashboard import summary

class CockpitTests(unittest.TestCase):
    def test_progress_and_receipts_no_payload_leak(self):
        with tempfile.TemporaryDirectory() as folder:
            j=Journal(Path(folder)/"state")
            plan={"id":"copilot-test","hosts":[],"limits":{
                "max_cost_usd":0,"max_seconds":30,"allow_browser_interaction":False},
                "steps":[{"id":"hash","kind":"digest","needs":[],
                          "input":{"text":"PRIVATE_DO_NOT_SEND"}}]}
            j.submit(plan,owner_approved=True)
            j.start_step("copilot-test","hash")
            j.finish_step("copilot-test","hash",ok=True,result={"ok":True,"sha256":"abc"})
            data=summary(j.path,metrics=lambda:{"mem_available_mb":8000,
                         "load_per_core":.1,"max_thermal_c":45})
            row=data["missions"][0]
            self.assertEqual(row["steps_total"],1)
            self.assertEqual(row["steps_completed"],1)
            self.assertEqual(row["evidence_receipts"],1)
            self.assertNotIn("PRIVATE_DO_NOT_SEND",str(data))
