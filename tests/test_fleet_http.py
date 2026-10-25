"""HTTP app-worker availability must be authenticated and device-bound."""
from tests.test_web_console import DashboardHTTPTests

class FleetHTTPTests(DashboardHTTPTests):
    def test_scoped_device_http_admission(self):
        broker=self.console.apps
        app="edge-http-proof"
        broker.register(app,["json.inspect"],confirm=True)
        broker.set_task_budget(app,"json.inspect",128,confirm=True)
        submit=broker.issue(app,"submit",confirm=True)["token"]
        worker=broker.issue(app,"worker",confirm=True)["token"]
        broker.enroll_edge(app,"windows_arm64",confirm=True)
        headers={"Authorization":"Bearer "+worker}
        report={"platform":"Windows","architecture":"arm64",
                "free_ram_mb":4096,"charging":True}
        code,_,_=self.request("POST","/api/v1/workers/availability",report)
        self.assertEqual(code,401)
        code,_,_=self.request("POST","/api/v1/workers/availability",report,
                         headers={"Authorization":"Bearer "+submit})
        self.assertEqual(code,403)
        invalid={**report,"architecture":"x86_64"}
        code,_,_=self.request("POST","/api/v1/workers/availability",invalid,
                         headers=headers)
        self.assertEqual(code,400)
        code,answer,_=self.request("POST","/api/v1/workers/availability",report,
                             headers=headers)
        self.assertEqual(code,200)
        self.assertTrue(answer["online"])
        code,created,_=self.request("POST","/api/v1/jobs",
                          {"task":"json.inspect","payload":{"text":"{}"}},
                          headers={"Authorization":"Bearer "+submit})
        self.assertEqual(code,201)
        code,claimed,_=self.request("POST","/api/v1/workers/claim",{},
                              headers=headers)
        self.assertEqual(code,200,claimed)
        self.assertEqual(claimed["job"]["job_id"],created["job_id"])
        code,receipt,_=self.request("POST","/api/v1/workers/complete",
                {"job_id":created["job_id"],"lease_token":claimed["job"]["lease_token"],
                 "success":True,"result":{"valid":True}},headers=headers)
        self.assertEqual(code,200,receipt)
        self.assertEqual(receipt["status"],"completed")
