"""Free-only router: selection, outage/recovery, data boundaries, no-spend controls."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

import free_ai_router as router

NOW=1793234000

class FreeRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)/"state/free-ai"
        p=patch.object(router,"STATE_DIR",self.state)
        p.start()
        self.addCleanup(p.stop)

    def test_local_fallback_timeout_fails_without_crashing_router(self):
        def timeout(*args,**kwargs):
            raise subprocess.TimeoutExpired(args[0],kwargs.get("timeout",48))
        self.assertIsNone(router._local_request("Harmless test",64,runner=timeout))

    def test_metadata_zero_dollars_required(self):
        class Resp:
            status=200
            def __init__(self,price): self.price=price
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return json.dumps({"data":[{"id":"openrouter/free",
                      "pricing":self.price}]}).encode()
        class Client:
            def __init__(self,price): self.price=price
            def open(self,*args,**kwargs): return Resp(self.price)
        with patch.object(router,"_read_key",return_value="TEST_KEY"):
            with patch.object(router,"_client",return_value=Client({"prompt":"0","completion":"0"})):
                self.assertEqual(router._metadata("openrouter"),(True,"zero_price_catalog_confirmed"))
            with patch.object(router,"_client",return_value=Client({"prompt":"0.0001","completion":"0"})):
                ok,why=router._metadata("openrouter")
                self.assertFalse(ok);self.assertIn("nonzero",why)
            with patch.object(router,"_client",return_value=Client({"prompt":"0"})):
                self.assertFalse(router._metadata("openrouter")[0])

    def test_monitor_disables_then_restores_verified_free_metadata(self):
        with patch.object(router,"_safe_activation",return_value=True):
            values={"openrouter":(True,"zero_price_catalog_confirmed"),
                    "nvidia":(True,"model_present_developer_prototype_only"),
                    "gemini":(True,"model_present_billing_tier_unverified"),
                    "groq":(True,"model_present_billing_tier_unverified")}
            first=router.monitor(NOW,fetcher=lambda p:values[p])
            status={i["provider"]:i for i in first}
            self.assertTrue(status["openrouter"]["eligible"])
            self.assertTrue(status["nvidia"]["eligible"])
            self.assertFalse(status["gemini"]["eligible"])
            self.assertFalse(status["groq"]["eligible"])
            self.assertFalse(next(i for i in router.check_status(NOW+2800)
                                  if i["provider"]=="openrouter")["eligible"])
            values["openrouter"]=(False,"nonzero_or_unverified_pricing")
            blocked=router.monitor(NOW+3000,fetcher=lambda p:values[p])
            self.assertFalse(next(x for x in blocked if x["provider"]=="openrouter")["eligible"])
            values["openrouter"]=(True,"zero_price_catalog_confirmed")
            restored=router.monitor(NOW+3900,fetcher=lambda p:values[p])
            self.assertTrue(next(x for x in restored if x["provider"]=="openrouter")["eligible"])

    def test_no_cloud_for_private_tasks_even_when_explicit_flag_present(self):
        with patch.object(router,"check_status",side_effect=AssertionError("cloud status accessed")):
            result=router.ask("coding","private proprietary code",allow_cloud=True,
                              data_class="private",purpose="prototype",
                              local=lambda prompt,tokens:"OFFLINE_OK",now=NOW)
        self.assertEqual(result["source"],"samsung-local")
        self.assertEqual(result["attempts"],[])

    def test_production_never_uses_nvidia_prototyping_endpoint(self):
        sample={p:{"provider":p,"eligible":True} for p in router.PROVIDERS}
        sample_list=list(sample.values())
        seen=[]
        with patch.object(router,"check_status",return_value=sample_list), \
             patch.object(router,"_reserve",return_value=(True,"reserved")):
            result=router.ask("coding","clean function",purpose="production",allow_cloud=True,
                              data_class="public",now=NOW,local=lambda *args:None,
                              cloud=lambda p,q,n: (seen.append(p) or "OK","ok",200))
        self.assertEqual(seen,["openrouter"])
        self.assertEqual(result["source"],"openrouter")

    def test_prototype_reasoning_prefers_nvidia_with_verified_catalog(self):
        sample=[{"provider":p,"eligible":p in ("nvidia","openrouter")} for p in router.PROVIDERS]
        with patch.object(router,"check_status",return_value=sample), \
             patch.object(router,"_reserve",return_value=(True,"reserved")):
            result=router.ask("reasoning","plan logic",purpose="prototype",allow_cloud=True,
                              data_class="public",now=NOW,local=lambda *a:None,
                              cloud=lambda p,q,n:("NVIDIA_OK","ok",200))
        self.assertEqual(result["source"],"nvidia")

    def test_provider_outage_falls_back_and_obeys_cooldown(self):
        sample=[{"provider":p,"eligible":p=="openrouter"} for p in router.PROVIDERS]
        with patch.object(router,"check_status",return_value=sample):
            first=router.ask("chat","Hello",purpose="production",allow_cloud=True,data_class="public",
                             now=NOW,local=lambda *a:"LOCAL_OK",
                             cloud=lambda *a:(None,"http_429",429))
            self.assertEqual(first["source"],"samsung-local")
            self.assertEqual(first["attempts"][0]["outcome"],"http_429")
            second=router.ask("chat","Hello",purpose="production",allow_cloud=True,data_class="public",
                              now=NOW+20,local=lambda *a:"LOCAL_OK",
                              cloud=lambda *a: (_ for _ in ()).throw(AssertionError("rate limit ignored")))
            self.assertEqual(second["source"],"samsung-local")
            self.assertIn(second["attempts"][0]["outcome"],("cooldown","rate_spaced"))

    def test_quota_stops_automatic_cloud_requests(self):
        sample=[{"provider":p,"eligible":p=="openrouter"} for p in router.PROVIDERS]
        with patch.object(router,"check_status",return_value=sample):
            for i in range(router.OPENROUTER_DAILY_CAP):
                outcome=router.ask("chat","hi",allow_cloud=True,data_class="public",now=NOW+i*90,
                                   cloud=lambda *a:("OK","ok",200),local=lambda *a:"LOCAL")
                self.assertEqual(outcome["source"],"openrouter")
            outcome=router.ask("chat","hi",allow_cloud=True,data_class="public",
                               now=NOW+router.OPENROUTER_DAILY_CAP*90,
                               cloud=lambda *a: (_ for _ in ()).throw(AssertionError("quota bypass")),
                               local=lambda *a:"LOCAL")
            self.assertEqual(outcome["source"],"samsung-local")
            self.assertEqual(outcome["attempts"][0]["outcome"],"daily_limit")

    def test_free_tier_attestation_expires_not_perpetual(self):
        with patch.object(router,"_safe_activation",return_value=True):
            router.monitor(NOW,fetcher=lambda p:(True,"model_present_billing_tier_unverified"))
            self.assertFalse(next(x for x in router.check_status(NOW) if x["provider"]=="groq")["eligible"])
            router.attest_tier("groq",free=True,no_paid_billing=True,now=NOW)
            self.assertTrue(next(x for x in router.check_status(NOW) if x["provider"]=="groq")["eligible"])
            # Recheck fresh catalog, but free-tier attestation still expires
            router.monitor(NOW+24*3600+1,fetcher=lambda p:(True,"model_present_billing_tier_unverified"))
            self.assertFalse(next(x for x in router.check_status(NOW+24*3600+1)
                                  if x["provider"]=="groq")["eligible"])

    def test_auto_task_classification_is_local_and_no_cloud_default(self):
        self.assertEqual(router.classify("Write Python code for a function"),"coding")
        self.assertEqual(router.classify("Translate Hello into French"),"translation")
        self.assertEqual(router.classify("Summarise this email"),"chat")
        reply=router.ask("auto","Write Python code",now=NOW,local=lambda *a:"LOCAL")
        self.assertEqual(reply["task"],"coding")
        self.assertEqual(reply["source"],"samsung-local")

    def test_openrouter_enforces_provider_zero_price_cap(self):
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'{"choices":[{"message":{"content":"READY"}}]}'
        class Client:
            def __init__(self): self.req=None
            def open(self,req,timeout):
                self.req=req
                return Response()
        client=Client()
        with (patch.object(router,"_read_key",return_value="TEST_ONLY_KEY_LONG_123"),
              patch.object(router,"_client",return_value=client)):
            result=router._cloud_request("openrouter","harmless prompt",128)
        self.assertEqual(result[0],"READY")
        body=json.loads(client.req.data)
        self.assertEqual(body["model"],"openrouter/free")
        self.assertEqual(body["provider"]["max_price"],{"prompt":0,"completion":0})
        self.assertNotIn("TEST_ONLY_KEY_LONG_123",str(body))

    def test_no_paid_fallback_and_input_bounds(self):
        with self.assertRaises(ValueError):
            router.ask("chat","x"*6001,local=lambda *a:"")
        with self.assertRaises(ValueError):
            router.ask("chat","hi",max_tokens=257,local=lambda *a:"")
        with self.assertRaises(ValueError):
            router.ask("superuser","hi",local=lambda *a:"")
        result=router.ask("chat","hi",local=lambda *a:"",allow_cloud=False,now=NOW)
        self.assertFalse(result["ok"])
        self.assertFalse(result["paid_fallback"])

if __name__=="__main__":
    unittest.main()
