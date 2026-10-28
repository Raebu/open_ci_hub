"""Full account-level suppression readback remains no-send and privacy-safe."""
from pathlib import Path
import tempfile
import unittest

import business_channels as ch
from tests.test_business_channels import KEY,cfg


class SuppressionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger=ch.ChannelLedger(Path(self.tmp.name)/"private.sqlite")
        self.account=ch.validate_config(cfg())["accounts"][1]

    def test_resend_reads_inbound_plus_paginated_suppressions_and_holds(self):
        calls=[]
        def get(provider,path,token):
            calls.append((provider,path))
            if path=="/emails?limit=12":
                return {"data":[]}
            if path=="/emails/receiving?limit=12":
                return {"data":[
                    {"id":"received1","from":"client@example.org",
                     "to":["support@example.org"],
                     "message_id":"<reply@example.org>","created_at":"2026-10-28T06:10:00Z"},
                    {"id":"excluded","from":"stranger@another.example",
                     "to":["editorial@outside.example"]}]}
            if path=="/suppressions?limit=100":
                return {"data":[{"id":"sup1","email":"stop@example.org",
                                 "origin":"complaint"}],"has_more":True}
            if path=="/suppressions?limit=100&after=sup1":
                return {"data":[{"id":"sup2","email":"bounce@example.org",
                                 "origin":"bounce"}],"has_more":False}
            raise AssertionError(path)
        messages,suppressed=ch.fetch_resend(self.account,"token",get=get)
        self.assertEqual(len(messages),1)
        self.assertEqual(len(suppressed),2)
        normalized=[ch.normalized_event(self.account,m,KEY) for m in messages]
        self.assertEqual(self.ledger.add(normalized),1)
        self.assertEqual(self.ledger.add_suppressions(suppressed,KEY),2)
        self.assertEqual(self.ledger.add_suppressions(suppressed,KEY),0)
        self.assertEqual(self.ledger.report()["distinct_holds"],3)
        self.assertTrue(self.ledger.eligible_to_contact("stop@example.org",KEY)["held"])
        self.assertTrue(self.ledger.eligible_to_contact("bounce@example.org",KEY)["held"])
        self.assertFalse(self.ledger.eligible_to_contact("unknown@example.org",KEY)["can_send"])
        raw=self.ledger.path.read_bytes()
        self.assertNotIn(b"stop@example.org",raw)
        self.assertNotIn(b"client@example.org",raw)
        self.assertTrue(any("/suppressions" in path for _,path in calls))

    def test_invalid_suppression_origin_fails_closed(self):
        with self.assertRaises(ch.ChannelError):
            self.ledger.add_suppressions([
                {"address":"bad@example.org","origin":"accepted"}],KEY)
        self.assertEqual(self.ledger.report()["distinct_holds"],0)

    def test_unsafe_or_incomplete_suppression_pagination_is_rejected(self):
        def bad_get(provider,path,token):
            if path.startswith("/emails"):
                return {"data":[]}
            return {"data":[],"has_more":True}
        with self.assertRaises(ch.ChannelError):
            ch.fetch_resend(self.account,"token",get=bad_get)


if __name__=="__main__":
    unittest.main()
