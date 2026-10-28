"""Reuse one existing LinkedIn OAuth authorisation; never clone an account."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
import business_social as social


class ExistingPublisherTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        (self.root/"data").mkdir(mode=0o700)
        self.env=self.root/".env"
        self.env.write_text("LINKEDIN_PERSON_URN=urn:li:person:TestPerson123\n")
        self.env.chmod(0o600)
        self.db=self.root/"data"/"autoposter.sqlite"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE linkedin_tokens(target TEXT PRIMARY KEY, access_token TEXT,expires_at INTEGER,scope TEXT)")
            db.execute("INSERT INTO linkedin_tokens VALUES (?,?,?,?)",("personal","A"*32,1799999999,"openid profile w_member_social_feed"))
        self.db.chmod(0o600)

    def test_existing_oauth_valid_only_with_private_app_and_scope(self):
        r=social.existing_publisher_token(root=self.root,clock=lambda:1793175600)
        self.assertIsNotNone(r)
        self.assertEqual(r["member"],"urn:li:person:TestPerson123")
        self.assertEqual(r["source"],"existing_Raeburn_LinkedIn_Dual_AutoPoster")
        self.db.chmod(0o644)
        self.assertIsNone(social.existing_publisher_token(root=self.root,clock=lambda:1793175600))
        self.db.chmod(0o600)
        self.assertIsNone(social.existing_publisher_token(root=self.root,clock=lambda:1800000000))
        self.env.write_text("LINKEDIN_PERSON_URN=urn:li:organization:100\n")
        self.assertIsNone(social.existing_publisher_token(root=self.root,clock=lambda:1793175600))

    def test_publisher_consolidation_policy_idempotent(self):
        import json
        old={**social.approved_policy(),"allowed_actions":["publish","like","comment","comment_reply"]}
        p=self.root/"social-policy.json"
        p.write_text(json.dumps(old));p.chmod(0o600)
        self.assertEqual(social.provision_policy(confirm=True,path=p)["migration"],"publisher_consolidated")
        self.assertTrue(social.provision_policy(confirm=True,path=p)["existing"])
        self.assertEqual(json.loads(p.read_text())["allowed_actions"],["like","comment","comment_reply"])


if __name__=="__main__":
    unittest.main()
