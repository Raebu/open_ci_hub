"""Backward-compatible, pinned offline images per CI job step."""
import json
import tempfile
import unittest
from pathlib import Path

import ci_bridge


class StepImageValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "manifest.json"
        self.payload = {
            "schema": 1,
            "repositories": {
                "Raebu/test-repo": {
                    "provider": "github",
                    "secret_env": "MY_LOCAL_WEBHOOK_SECRET",
                    "branches": ["main"],
                    "image": "python:3.12-slim@sha256:" + "a" * 64,
                    "network": "offline",
                    "ram_mb": 768,
                    "steps": [
                        {"name": "python-tests", "argv": ["python", "-m", "unittest"]},
                        {"name": "wallet-node-tests",
                         "image": "node:24-bookworm-slim@sha256:" + "b" * 64,
                         "argv": ["node", "--test", "test/wallet.test.mjs"]},
                    ],
                }
            }
        }

    def load(self):
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")
        self.path.chmod(0o600)
        return ci_bridge.load_manifest(self.path)

    def test_different_runtime_images_pinned_per_step(self):
        data = self.load()
        steps = data["repositories"]["Raebu/test-repo"]["steps"]
        self.assertNotIn("image", steps[0])
        self.assertTrue(steps[1]["image"].startswith("node:24-bookworm-slim@sha256:"))

    def test_unpinned_image_rejected(self):
        for invalid in ("node:latest", "", None, "sha256:" + "c" * 64):
            with self.subTest(image=invalid):
                self.payload["repositories"]["Raebu/test-repo"]["steps"][1]["image"] = invalid
                with self.assertRaises(ValueError):
                    self.load()

    def test_explicit_network_and_resource_guards_unchanged(self):
        data = self.load()
        cfg = data["repositories"]["Raebu/test-repo"]
        self.assertEqual(cfg["network"], "offline")
        self.assertEqual(cfg["ram_mb"], 768)


if __name__ == "__main__":
    unittest.main()
