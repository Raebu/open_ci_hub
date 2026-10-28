"""Acceptance tests for user-authorised one-to-one outbound.

No live providers contacted; prove duplicate, security, source and crash gates.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import business_outbound as b
from business_channels import ChannelLedger, fingerprint

AES=b"a"*32
HMAC=b"h"*32
NOW=1793179200   # fixed illustrative time; schedule from a real London weekday


def workday():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026,10,28,10,15,tzinfo=ZoneInfo("Europe/London")).timestamp()


def example(now=None):
    now=workday() if now is None else now
    return {
        "case_id":"RCGL-CLIENT-20261028-0001","lane":"sales",
        "kind":"initial","sender":"raeburn-primary",
        "to":"director@client-limited.example",
        "subject":"Relevant operations improvement",
        "body":"Hello, I noted an opportunity to help your team's processes.\n"
               "If it is not relevant, reply 'do not contact' and I will stop further messages.\n"
               "Kind regards, Raeburn Group",
        "company_domain":"client-limited.example",
        "subscriber_category":"uk_corporate",
        "lawful_basis":"legitimate_interests",
        "source_url":"https://find-and-update.company-information.service.gov.uk/company/01234567",
        "canonical_owner":"open_ci_hub",
        "handoff_verified_at":now-60,
        "last_inbox_readback_at":now-60,
        "last_suppression_readback_at":now-60,
        "explicit_opt_out":False,"prior_attempts":0,
        "initial_sent_at":None,"last_human_reply_at":None,
        "due_at":now-10,"in_reply_to":None,"thread_id":None
    }


class OutboundTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp=Path(tmp.name)
        self.now=workday()
        self.db=self.tmp/"outbound.sqlite"
        self.policy=b.owner_policy()
        self.outbox=b.Outbox(self.db,key=AES,identity_key=HMAC,clock=lambda:self.now)
        self.channel=ChannelLedger(self.tmp/"source.sqlite")

    def test_policy_requires_exact_sender_rules_and_owner_consent(self):
        self.assertEqual(self.policy["mode"],"autonomous_qualified")
        self.assertEqual(self.policy["sender_permissions"]["raeburn-primary"],
                         ["initial","followup","reply"])
        for adjustment in (
            lambda p:p.update(mode="unrestricted"),
            lambda p:p["sender_permissions"]["raeburn-contact"].append("initial"),
            lambda p:p.update(max_daily=100),
            lambda p:p.update(max_per_run=20),
            lambda p:p.update(chatgpt_parallel_migration=False)
        ):
            import copy
            modified=copy.deepcopy(self.policy)
            adjustment(modified)
            with self.assertRaises(b.OutboundRefused):
                b.safe_config(modified)

    def test_reject_suppressed_unverified_cold_contact(self):
        t=example(self.now)
        for change in (
            {"to":"person@different.example"},
            {"canonical_owner":"chatgpt_sales"},
            {"explicit_opt_out":True},
            {"handoff_verified_at":self.now-3600},
            {"last_suppression_readback_at":self.now-3600},
            {"source_url":"http://unverified"},
            {"lawful_basis":"none"},
            {"subject":"Hello\nBcc:someone@other.example"},
            {"body":"Buy now!"},
            {"subscriber_category":"opted_in","lawful_basis":"legitimate_interests"}
        ):
            copy={**t,**change}
            with self.assertRaises(b.OutboundRefused):
                self.outbox.enqueue(copy,self.policy)

    def test_payload_encrypted_deduplicated_and_private(self):
        once=self.outbox.enqueue(example(self.now),self.policy)
        twice=self.outbox.enqueue(example(self.now),self.policy)
        self.assertEqual(once["state"],"queued")
        self.assertFalse(once["duplicate"])
        self.assertTrue(twice["duplicate"])
        self.assertEqual(once["id"],twice["id"])
        self.assertEqual(self.outbox.path.stat().st_mode&0o777,0o600)
        raw=self.outbox.path.read_bytes()
        self.assertNotIn(b"director@client",raw)
        self.assertNotIn(b"Relevant operations improvement",raw)
        self.assertNotIn(b"RCGL-CLIENT-20261028",raw)

    def test_unconnected_providers_cannot_send_despite_owner_enabled(self):
        self.outbox.enqueue(example(self.now),self.policy)
        actions=[]
        with patch.object(b,"ChannelLedger",return_value=self.channel):
            result=self.outbox.dispatch(
                policy=self.policy,credential=lambda alias:"fake-token",
                sender=lambda *args:actions.append(args),now=self.now)
        self.assertEqual(result["attempted"],0)
        self.assertEqual(result["accepted_pending_delivery"],0)
        self.assertIn("all_provider_readback_required",result["blocked"])
        self.assertEqual(actions,[])

    def test_holiday_or_weekend_refuses_sending(self):
        saturday=__import__("datetime").datetime(2026,10,31,10,tzinfo=__import__("zoneinfo").ZoneInfo("Europe/London")).timestamp()
        self.assertFalse(b.in_uk_business_hours(saturday))
        self.assertFalse(b.in_uk_business_hours(
            __import__("datetime").datetime(2026,10,28,20,tzinfo=__import__("zoneinfo").ZoneInfo("Europe/London")).timestamp()))

    def mark_all_providers_ready(self):
        for alias,provider in (("raeburn-primary","gmail"),
                               ("raeburn-contact","gmail"),
                               ("raeburn-resend","resend")):
            with self.channel.db() as db:
                db.execute("INSERT INTO sync VALUES(?,?,?,?,?,?)",
                           (alias,provider,"authenticated_read_only",12,self.now-60,
                            "Verified provider metadata"))

    def test_verified_send_reservation_and_no_automatic_second_send(self):
        self.outbox.enqueue(example(self.now),self.policy)
        self.mark_all_providers_ready()
        calls=[]
        def send(alias,token,payload,jid):
            calls.append((alias,jid))
            return {"provider":"gmail","message_id":"id-abc124",
                    "thread_id":"t-123"}
        with patch.object(b,"ChannelLedger",return_value=self.channel):
            result=self.outbox.dispatch(policy=self.policy,
                                        credential=lambda _: "secret-not-printed",
                                        sender=send,now=self.now)
            repeat=self.outbox.dispatch(policy=self.policy,
                                        credential=lambda _: "secret-not-printed",
                                        sender=send,now=self.now+200)
        self.assertEqual(result["attempted"],1)
        self.assertEqual(result["accepted_pending_delivery"],1)
        self.assertEqual(repeat["attempted"],0)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.outbox.report()["queue_counts"],
                         {"accepted_pending_delivery":1})

    def test_failed_provider_response_never_retried_blindly(self):
        self.outbox.enqueue(example(self.now),self.policy)
        self.mark_all_providers_ready()
        calls=[]
        def send(*args):
            calls.append(1)
            raise TimeoutError("simulated request timeout")
        with patch.object(b,"ChannelLedger",return_value=self.channel):
            result=self.outbox.dispatch(policy=self.policy,
                        credential=lambda _: "secret",sender=send,now=self.now)
            second=self.outbox.dispatch(policy=self.policy,
                        credential=lambda _: "secret",sender=send,now=self.now+400)
        self.assertEqual(result["attempted"],1)
        self.assertIn("uncertain_provider_result_manual_reconcile",result["blocked"])
        self.assertEqual(second["attempted"],0)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.outbox.report()["queue_counts"],
                         {"dispatch_uncertain":1})

    def test_cold_followup_requires_day3_or_day7_and_no_reply(self):
        d=example(self.now)
        d.update(kind="followup",initial_sent_at=self.now-2*86400,
                 prior_attempts=1)
        with self.assertRaises(b.OutboundRefused):
            self.outbox.enqueue(d,self.policy)
        d["initial_sent_at"]=self.now-4*86400
        x=self.outbox.enqueue(d,self.policy)
        self.assertEqual(x["state"],"queued")
        d["last_human_reply_at"]=self.now-50
        with self.assertRaises(b.OutboundRefused):
            self.outbox.enqueue(d,self.policy)

    def test_sender_provider_constructs_gmail_threading_and_resend_idempotency(self):
        calls=[]
        class Response:
            status=200
            def read(self,n):return b'{"id":"provider567","threadId":"thread1"}'
        class Connection:
            def __init__(self,host,timeout):self.host=host;calls.append(self)
            def request(self,method,path,body,headers):
                self.method=method;self.path=path;self.body=json.loads(body)
                self.headers=headers
            def getresponse(self):return Response()
            def close(self):pass
        payload=example(self.now)
        payload.update(in_reply_to="<message123@example.org>",thread_id="thread1")
        v=b.outbound_post("raeburn-primary","token",payload,"key123",conn=Connection)
        self.assertEqual(v["provider"],"gmail")
        self.assertEqual(calls[0].method,"POST")
        self.assertEqual(calls[0].body["threadId"],"thread1")
        self.assertEqual(calls[0].headers.get("Idempotency-Key"),None)
        calls.clear()
        v=b.outbound_post("raeburn-resend","token",payload,"key123",conn=Connection)
        self.assertEqual(calls[0].headers["Idempotency-Key"],"key123")
        self.assertEqual(calls[0].body["from"],"support@theraeburngroup.com")

    def test_encryption_key_is_independent_from_identity_key(self):
        # Never persist PII as plaintext or replace an existing key.
        self.assertNotEqual(self.outbox.key,self.outbox.identity_key)
        path=self.tmp/"bad-alias"
        path.symlink_to(self.db)
        with self.assertRaises(PermissionError):
            b.Outbox(path,key=AES,identity_key=HMAC)


if __name__=="__main__":
    unittest.main()
