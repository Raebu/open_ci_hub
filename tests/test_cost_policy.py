"""Cost denials must happen before provider requests or credential access."""
import unittest
from unittest.mock import Mock, patch
from deployment_providers import CloudflareVersions, VercelAlias
from operations import pipeline_action
from providers.circleci_cockpit import pipeline_action as circle_action

class CostPolicyTests(unittest.TestCase):
    def test_all_provider_retries_block_before_cli(self):
        with patch('operations.invoke') as invoke, patch('operations.invoke_json') as query:
            for provider in ('github', 'gitlab', 'circleci'):
                with self.subTest(provider=provider), self.assertRaises(PermissionError):
                    pipeline_action(provider, 'owner/repo', '123', 'retry')
            with self.assertRaises(PermissionError):
                circle_action('owner/repo', '12345678-1234-1234-1234-123456789012', 'retry')
            invoke.assert_not_called(); query.assert_not_called()

    def test_cloud_mutations_and_rollbacks_block_before_credentials(self):
        creds=Mock(); request=Mock()
        adapters=[CloudflareVersions({'account_id':'a'*32,'script':'test-worker'},creds,request),
                  VercelAlias({'alias':'preview.example.org'},creds,request)]
        for adapter in adapters:
            for method in (adapter.promote, adapter.rollback):
                with self.assertRaisesRegex(PermissionError, 'Zero-cost policy'):
                    method('12345678-1234-1234-1234-123456789012')
        request.assert_not_called(); creds.token.assert_not_called()

    def test_cancellation_stays_available(self):
        with patch('operations.invoke') as invoke:
            pipeline_action('github','owner/repo',123,'cancel')
            self.assertEqual(invoke.call_args.args[0][2], 'cancel')

class RepositoryCredentialTests(unittest.TestCase):
    def test_checkout_uses_cli_helper_without_token_or_global_config(self):
        from ci_bridge import repository_clone_argv, run_cmd
        for provider,cli in (("github","gh"),("gitlab","glab")):
            argv=repository_clone_argv(provider,"owner/repo","/tmp/work")
            self.assertIn("credential.helper=!"+cli+" auth git-credential",argv)
            self.assertIn("credential.helper=",argv)
            self.assertNotIn("--global",argv)
        with patch('ci_bridge.subprocess.run') as run:
            run_cmd(["git","version"])
            self.assertEqual(run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
        with self.assertRaises(ValueError):
            repository_clone_argv("gitlab","--bad/repo;cmd","/tmp/work")
