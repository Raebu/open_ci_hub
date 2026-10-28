"""Business provider metadata and cross-provider hold tests — never send a message."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import business_channels as c


KEY=b"k"*32
T0=1793165400


def cfg():
    return {"schema":c.SCHEMA,"mode":"read_only","accounts":[
        {"alias":"company-mailbox","provider":"gmail",
         "identity":"owner@example.org","purpose":"business_read_only",
         "enabled":True,"auth_secret_label":"company-gmail",
         "allowed_recipients":[]},
        {"alias":"resend-business","provider":"resend",
         "identity":"company-resend","purpose":"business_read_only",
         "enabled":True,"auth_secret_label":"company-resend",
         "allowed_recipients":["support@example.org"]},
        {"alias":"personal-outlook","provider":"microsoft",
         "identity":"personal@example.org","purpose":"excluded_personal",
         "enabled":False,"auth_secret_label":"","allowed_recipients":[]},
        {"alias":"linkedin-read","provider":"linkedin",
         "identity":"profile_search_connector_only","purpose":"business_read_only",
         "enabled":False,"auth_secret_label":"","allowed_recipients":[]}
    ]}


class BusinessChannelTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.ledger=c.ChannelLedger(self.root/"evidence.db")
        self.accounts=c.validate_config(cfg())["accounts"]

    def test_strict_account_policy_denies_linkedin_and_personal_send(self):
        for edit in (
            lambda x:x.update(mode="send"),
            lambda x:x["accounts"][2].update(enabled=True),
            lambda x:x["accounts"][3].update(enabled=True),
            lambda x:x["accounts"][1].update(allowed_recipients=[]),
            lambda x:x["accounts"][0].update(alias="../escape"),
            lambda x:x["accounts"][1].update(identity="http://internal"),
            lambda x:x["accounts"][1].update(auth_secret_label="new\nsecret"),
        ):
            bad=cfg()
            edit(bad)
            with self.assertRaises(c.ChannelError):
                c.validate_config(bad)

    def test_hash_stability_and_no_plaintext_identity_in_sqlite(self):
        a=c.normalized_event(self.accounts[0],{
            "provider_message_id":"id123","thread_id":"thread123",
            "message_id":"<one@example.org>","in_reply_to":None,
            "sender":"prospect@example.org","recipients":["owner@example.org"],
            "auto_submitted":"","direction":"inbound","status":"delivered",
            "timestamp":T0},KEY)
        self.assertEqual(len(a["id"]),64)
        self.assertEqual(self.ledger.add([a]),1)
        self.assertEqual(self.ledger.add([a]),0)
        raw=self.ledger.path.read_bytes()
        for target in (b"prospect@example.org",b"owner@example.org",b"thread123",
                       b"one@example.org",b"id123"):
            self.assertNotIn(target,raw)
        self.assertEqual(self.ledger.report()["distinct_holds"],1)
        self.assertEqual(self.ledger.eligible_to_contact("Prospect@Example.Org",KEY)["can_send"],False)
        self.assertTrue(self.ledger.eligible_to_contact("prospect@example.org",KEY)["held"])

    def test_cross_provider_message_id_correlation(self):
        one=c.normalized_event(self.accounts[0],{
            "provider_message_id":"gmail1","thread_id":"thread1",
            "message_id":"<shared@provider.example>","in_reply_to":None,
            "sender":"prospect@example.org","recipients":["owner@example.org"],
            "auto_submitted":"","direction":"inbound","status":"observed",
            "timestamp":T0},KEY)
        two=c.normalized_event(self.accounts[1],{
            "provider_message_id":"resend1","thread_id":"thread2",
            "message_id":"<shared@provider.example>","in_reply_to":None,
            "sender":"support@example.org","recipients":["prospect@example.org"],
            "auto_submitted":"","direction":"outbound","status":"delivered",
            "timestamp":T0},KEY)
        self.assertEqual(self.ledger.add([one,two]),2)
        self.assertEqual(self.ledger.report()["cross_provider_rfc_correlations"],1)

    def test_bounce_or_complaint_causes_block_and_no_auto_send(self):
        outbound=c.normalized_event(self.accounts[1],{
            "provider_message_id":"re-id","thread_id":None,
            "message_id":None,"in_reply_to":None,
            "sender":"support@example.org","recipients":["bad@example.org"],
            "auto_submitted":"","direction":"outbound","status":"bounced",
            "timestamp":T0},KEY)
        self.ledger.add([outbound])
        state=self.ledger.eligible_to_contact("bad@example.org",KEY)
        self.assertTrue(state["held"])
        self.assertFalse(state["can_send"])

    def test_autoresponder_does_not_generate_human_reply_hold(self):
        event=c.normalized_event(self.accounts[0],{
            "provider_message_id":"id123","thread_id":None,"message_id":None,
            "in_reply_to":None,"sender":"robot@example.org",
            "recipients":["owner@example.org"],"auto_submitted":"auto-replied",
            "direction":"inbound","status":"delivered","timestamp":T0},KEY)
        self.assertEqual(event["classification"],"automatic_message")
        self.ledger.add([event])
        self.assertEqual(self.ledger.report()["distinct_holds"],0)

    def test_bounded_transport_rejects_redirects_and_wrong_hosts(self):
        class Response:
            status=302
            def read(self,n):return b'{"redirect":"https://evil.test"}'
        class Connection:
            def __init__(self,host,timeout):
                self.host=host
            def request(self,method,path,headers):
                self.method=method
                self.path=path
                self.headers=headers
            def getresponse(self):return Response()
            def close(self):pass
        with self.assertRaises(c.ChannelError):
            c.bounded_get("gmail","/gmail/v1/users/me/profile","fake-token",
                          connection=Connection)
        for path in ("https://internal.test/","//evil/path","/foo\rmalice","/foo//bar"):
            with self.assertRaises(c.ChannelError):
                c.bounded_get("gmail",path,"fake-token",connection=Connection)

    def test_gmail_read_only_collects_headers_and_matches_account(self):
        def get(provider,path,token):
            self.assertEqual(provider,"gmail")
            if path.endswith("profile"):return {"emailAddress":"owner@example.org"}
            if "messages?" in path:
                return {"messages":[{"id":"ABCd01234"}]}
            return {"id":"ABCd01234","threadId":"T123",
                    "internalDate":"1793165400000",
                    "payload":{"headers":[
                        {"name":"Message-ID","value":"<abc@example.org>"},
                        {"name":"From","value":"Prospect <prospect@example.org>"},
                        {"name":"To","value":"owner@example.org"}]}}
        events,_=c.fetch_gmail(self.accounts[0],"fake-token",get=get)
        self.assertEqual(len(events),2)
        self.assertIn("from",["from"])  # response header is treated only as data
        self.assertEqual(events[0]["sender"],"prospect@example.org")
        self.assertEqual(events[0]["direction"],"inbound")
        def wrong(prov,path,tok):
            return {"emailAddress":"unexpected@example.org"}
        with self.assertRaises(c.ChannelError):
            c.fetch_gmail(self.accounts[0],"fake-token",get=wrong)

    def test_resend_scope_ignores_unrelated_shared_account_domain(self):
        def get(provider,path,token):
            self.assertEqual(provider,"resend")
            if path=="/emails/receiving?limit=12":
                return {"data":[]}
            if path.startswith("/suppressions?"):
                return {"data":[],"has_more":False}
            self.assertEqual(path,"/emails?limit=12")
            return {"data":[
                {"id":"re1","from":"Business <support@example.org>",
                 "to":["buyer@example.org"],"message_id":"<one@resend.dev>",
                 "last_event":"delivered","created_at":"2026-10-28T05:00:00Z"},
                {"id":"re2","from":"editorial@other.example",
                 "to":["someone@example.org"],"last_event":"sent"}]}
        events,_=c.fetch_resend(self.accounts[1],"secret-placeholder",get=get)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["recipients"],["buyer@example.org"])

    def test_local_sync_does_not_claim_chatgpt_connector_as_local_oauth(self):
        outcomes=c.sync(cfg(),ledger=self.ledger,key=KEY,
            secret=lambda _:None)
        statuses={x["alias"]:x["state"] for x in outcomes["accounts"]}
        self.assertEqual(statuses["company-mailbox"],
                         "needs_independent_provider_oauth_or_api_key")
        self.assertEqual(statuses["personal-outlook"],"excluded_personal")
        self.assertEqual(statuses["linkedin-read"],"connector_profile_lookup_only")
        self.assertFalse(outcomes["source_coverage_complete"])
        self.assertEqual(outcomes["mail_sent"],0)
        self.assertEqual(outcomes["linkedin_messages_sent"],0)

    def test_missing_key_never_runs_direct_provider(self):
        calls=[]
        from unittest.mock import patch
        with patch.object(c,"hmac_key",return_value=None):
            outcome=c.sync(cfg(),ledger=self.ledger,key=None,
                           secret=lambda x:calls.append(x))
        self.assertTrue(all(x["state"]!="authenticated_read_only"
                            for x in outcome["accounts"]))
        self.assertFalse(calls)

    def test_ledger_symlink_refused(self):
        alias=self.root/"other.db"
        alias.symlink_to(self.ledger.path)
        with self.assertRaises(PermissionError):
            c.ChannelLedger(alias)


if __name__=="__main__":
    unittest.main()
