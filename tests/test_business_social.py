"""Official LinkedIn API action integration, with no website bots or DMs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import business_social as li
from business_outbound import OutboundRefused
from tests.test_business_outbound import workday

AES=b"a"*32
HMAC=b"h"*32
NOW=workday()
ACTOR="urn:li:person:ABC123identity"
TARGET="urn:li:activity:7345678901234567890"


def draft(kind="comment",now=NOW):
    return {
      "id":"RG-SOCIAL-20261028-001","kind":kind,
      "source_url":"https://www.linkedin.com/feed/update/urn:li:activity:7345678901234567890",
      "member_urn":ACTOR,
      "target_urn":TARGET if kind!="publish" else None,
      "text":"" if kind=="like" else
             "Pleased to discuss responsible automation for small businesses and the value of reliable, transparent workflows.",
      "approved_at":now-60,"scheduled_at":now-20,
      "verified_relevance":True,"verified_public_content":True,
      "brand":"Raeburn Group","source_case_id":"RG-MARKETING-20261028"
    }


class SocialTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp=Path(folder.name)
        self.ledger=li.SocialLedger(path=self.tmp/"outbound.sqlite",
                                    key=AES,identity_key=HMAC,clock=lambda:NOW)
        self.policy=li.approved_policy()

    def test_policy_excludes_unauthorised_dms_follow_and_browser(self):
        self.assertTrue({"comment_reply","comment","like"}.issubset(
            set(self.policy["allowed_actions"])))
        for name in ("private_messaging_enabled","website_bot_enabled",
                     "profile_follow_enabled"):
            self.assertFalse(self.policy[name])
        for altered in ("publish","direct_message","follow_member","connection_request"):
            action=draft("comment")
            action["kind"]=altered
            with self.assertRaises(OutboundRefused):
                self.ledger.enqueue(action,self.policy)

    def test_no_oauth_results_in_no_effects(self):
        self.ledger.enqueue(draft(),self.policy)
        attempted=[]
        result=self.ledger.dispatch(policy=self.policy,token_fn=lambda:None,
                                    call=lambda *args:attempted.append(args),now=NOW)
        self.assertEqual(result["state"],"official_member_oauth_required")
        self.assertEqual(attempted,[])

    def test_payload_encryption_and_deduplicated_queue(self):
        one=self.ledger.enqueue(draft(),self.policy)
        two=self.ledger.enqueue(draft(),self.policy)
        self.assertFalse(one["duplicate"])
        self.assertTrue(two["duplicate"])
        blob=self.ledger.path.read_bytes()
        for phrase in (b"responsible automation",b"RG-MARKETING",
                       b"urn:li:activity:7345678901234567890"):
            self.assertNotIn(phrase,blob)
        self.assertEqual(self.ledger.path.stat().st_mode&0o777,0o600)

    def test_posts_use_fixed_linkedin_rest_api(self):
        # Existing Raeburn LinkedIn AutoPoster is sole post publisher.
        self.assertNotIn("publish",self.policy["allowed_actions"])
        endpoint,body=li.prepare_request(draft("like"))
        self.assertTrue(endpoint.startswith("/rest/reactions?actor="))
        self.assertEqual(body["reactionType"],"LIKE")
        endpoint,body=li.prepare_request(draft("comment"))
        self.assertEqual(body["actor"],ACTOR)
        self.assertEqual(body["object"],TARGET)
        self.assertIn("/comments",endpoint)

    def test_identity_match_from_verified_official_oidc(self):
        token={"token":"owner-authorised-token","member":ACTOR,
               "scopes":["openid","w_member_social_feed"]}
        def good(method,path,t,body=None):
            return {"json":{"sub":"ABC123identity"}}
        self.assertTrue(li.verify_member(token,call=good))
        with self.assertRaises(OutboundRefused):
            li.verify_member(token,call=lambda *args:{"json":{"sub":"other"}})

    def test_official_action_receipt_and_no_second_action(self):
        self.ledger.enqueue(draft(),self.policy)
        record={"token":"local-secret-token","member":ACTOR,
                "scopes":["openid","w_member_social_feed"]}
        calls=[]
        def call(method,path,token,body=None):
            calls.append((method,path))
            if method=="GET":
                return {"status":200,"json":{"sub":"ABC123identity"}}
            return {"status":201,"restli_id":"urn:li:ugcPost:7345678901234567891",
                    "json":{}}
        first=self.ledger.dispatch(policy=self.policy,token_fn=lambda:record,
                                   call=call,now=NOW)
        second=self.ledger.dispatch(policy=self.policy,token_fn=lambda:record,
                                    call=call,now=NOW+50)
        self.assertEqual(first["accepted"],1)
        self.assertEqual(second["attempted"],0)
        self.assertEqual(len([x for x in calls if x[0]=="POST"]),1)
        self.assertEqual(self.ledger.status()["queue"],{"accepted":1})
        self.assertEqual(self.ledger.status()["publisher"],"existing_Raeburn_LinkedIn_Dual_AutoPoster")

    def test_uncertain_status_never_blindly_retried(self):
        self.ledger.enqueue(draft("comment"),self.policy)
        record={"token":"local-secret-token","member":ACTOR,
                "scopes":["openid","w_member_social_feed"]}
        def call(method,path,token,body=None):
            if method=="GET":
                return {"status":200,"json":{"sub":"ABC123identity"}}
            raise TimeoutError("transient")
        self.assertEqual(self.ledger.dispatch(policy=self.policy,
            token_fn=lambda:record,call=call,now=NOW)["attempted"],1)
        self.assertEqual(self.ledger.dispatch(policy=self.policy,
            token_fn=lambda:record,call=call,now=NOW+50)["attempted"],0)
        self.assertEqual(self.ledger.status()["queue"],{"uncertain":1})

    def test_bad_target_and_unverified_purpose_refused(self):
        for mutate in (
            lambda x:x.update(target_urn="https://evil.example/post"),
            lambda x:x.update(verified_relevance=False),
            lambda x:x.update(source_url="https://bad.example/blog"),
            lambda x:x.update(member_urn="urn:li:organization:123456"),
            lambda x:x.update(approved_at=NOW-9*86400)
        ):
            x=draft("like")
            mutate(x)
            with self.assertRaises(OutboundRefused):
                self.ledger.enqueue(x,self.policy)

    def test_http_enforces_official_host_headers_and_no_redirects(self):
        calls=[]
        class R:
            status=302
            def getheaders(self):return [("Location","https://bad.example")]
            def read(self,n):return b"{}"
        class C:
            def __init__(self,host,timeout):
                calls.append((host,timeout))
            def request(self,method,path,body=None,headers=None):
                self.headers=headers
            def getresponse(self):return R()
            def close(self):pass
        with self.assertRaises(OutboundRefused):
            li.official_request("POST","/rest/posts","token",{},conn=C)
        self.assertEqual(calls[0][0],"api.linkedin.com")
        with self.assertRaises(OutboundRefused):
            li.official_request("POST","https://linkedin.com/feed/","token",{},conn=C)

    def test_new_api_requires_explicit_oauth_scope_and_identity(self):
        def look(label,purpose):
            return '{"access_token":"1234567890123456","expires_at":9999999999,"member_urn":"urn:li:person:ABC123identity","granted_scopes":["openid"]}'
        self.assertIsNone(li.linkedin_token(lookup=look))
        def valid(label,purpose):
            return '{"access_token":"1234567890123456","expires_at":9999999999,"member_urn":"urn:li:person:ABC123identity","granted_scopes":["openid","w_member_social_feed"]}'
        self.assertEqual(li.linkedin_token(lookup=valid)["member"],ACTOR)


if __name__=="__main__":
    unittest.main()
