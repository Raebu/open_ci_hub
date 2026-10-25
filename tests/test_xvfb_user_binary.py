"""Tests for signed user-level Xvfb installation without privileged pacman."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import virtual_desktop as vd

class SignedUserXvfbTests(unittest.TestCase):
    def test_verified_user_binary_and_tamper_detection(self):
        with TemporaryDirectory() as temp:
            private=Path(temp)/"Xvfb"
            private.write_bytes(b"mock system-free signed Xvfb binary")
            private.chmod(0o700)
            expected=hashlib.sha256(private.read_bytes()).hexdigest()
            with (patch.object(vd,"BINARY",Path(temp)/"missing"),
                  patch.object(vd,"USER_BINARY",private),
                  patch.object(vd,"USER_BINARY_SHA256",expected)):
                self.assertEqual(vd.selected_binary(),private)
                self.assertTrue(vd.status()["binary_available"])
                private.write_bytes(b"tampered executable")
                self.assertIsNone(vd.selected_binary())
                self.assertFalse(vd.status()["binary_available"])
                private.unlink()
                private.symlink_to("/usr/bin/true")
                self.assertIsNone(vd.selected_binary())

    def test_unprivileged_package_is_available_on_owner_machine(self):
        # This is an integration probe, not a requirement on fresh CI hosts.
        if vd.USER_BINARY.is_file():
            self.assertIsNotNone(vd.selected_binary(),
                                 "Installed official Xvfb is no longer hash-valid.")

    def test_binary_missing_is_fail_closed(self):
        with TemporaryDirectory() as temp:
            with (patch.object(vd,"BINARY",Path(temp)/"missing-system"),
                  patch.object(vd,"USER_BINARY",Path(temp)/"missing-user")):
                self.assertIsNone(vd.selected_binary())
                self.assertFalse(vd.status()["binary_available"])
