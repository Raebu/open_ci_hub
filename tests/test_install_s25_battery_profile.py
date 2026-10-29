"""Termux S25 profile installer: reviewed sources, idempotence and backups."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/"scripts"/"install-s25-battery-profile.py"
spec=importlib.util.spec_from_file_location("install_s25_profile",FILE)
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
ROOT=Path(__file__).resolve().parents[1]


class TermuxInstallTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.prefix=self.root/"usr"
        self.home=self.root/"home"
        self.prefix.mkdir()
        self.home.mkdir()
        self.sv=self.prefix/"var/service/oci-edge-worker"
        self.sv.mkdir(parents=True)
        self.run=self.sv/"run"
        self.run.write_text(
            "#!/data/data/com.termux/files/usr/bin/sh\n"
            "export OCI_EDGE_URL=https://redacted.example.test\n"
            "export OCI_EDGE_WORKER_TOKEN=KEEP_PRIVATE_NOT_REAL\n"
            "exec python3 \"$HOME/worker/edge_worker.py\" --interval 45\n")
        self.run.chmod(0o700)
        self.checkout=self.home/"worker"
        self.checkout.mkdir()
        (self.checkout/"edge_worker.py").write_text(
            "MIN_BATTERY_PERCENT = 40\ndef phone_ready(): pass\n")
        (self.checkout/"application_client.py").write_text(
            "class ApplicationClient: pass\n")
        self.downloads=[]
        def fetch(url,timeout):
            self.downloads.append(url)
            assert url.startswith(installer.SOURCE_REPO)
            target=url.rsplit("/",1)[-1]
            return io.BytesIO((ROOT/target).read_bytes())
        self.fetch=fetch
        self.restarts=[]
        def restart(args,**kw):
            self.restarts.append(args)
            class Response:returncode=0
            return Response()
        self.restart=restart

    def install(self):
        with patch.object(installer,"phone_check",return_value="SM-S931B"):
            return installer.apply_update(
                prefix=self.prefix,home=self.home,urlopen=self.fetch,
                restart=self.restart,commit=True)

    def test_does_not_install_without_owner_flag(self):
        with self.assertRaises(installer.Refused):
            installer.apply_update(prefix=self.prefix,home=self.home)
        self.assertFalse((self.checkout/"edge_worker.py.before-s25-20pct").exists())

    def test_reviewed_sources_and_service_profile_idempotent(self):
        first=self.install()
        self.assertEqual(first["state"],"installed")
        self.assertEqual(first["reserve_percent"],20)
        self.assertFalse(first["charging_required"])
        self.assertEqual(self.restarts,[["sv","restart","oci-edge-worker"]])
        self.assertEqual((self.checkout/"edge_worker.py").read_bytes(),
                         (ROOT/"edge_worker.py").read_bytes())
        run_text=self.run.read_text()
        self.assertEqual(run_text.count(installer.TAG),1)
        self.assertIn("KEEP_PRIVATE_NOT_REAL",run_text)
        self.assertEqual(self.run.stat().st_mode&0o777,0o700)
        self.assertEqual((self.checkout/"edge_worker.py.before-s25-20pct")
                         .stat().st_mode&0o777,0o600)
        second=self.install()
        self.assertEqual(second["state"],"already_configured")
        self.assertEqual(len(self.restarts),1)

    def test_refuses_mismatched_source_before_file_mutation(self):
        original=self.run.read_bytes()
        def untrusted(url,timeout):
            return io.BytesIO(b"print('untrusted code')")
        with patch.object(installer,"phone_check",return_value="SM-S931B"):
            with self.assertRaises(installer.Refused):
                installer.apply_update(prefix=self.prefix,home=self.home,
                                       urlopen=untrusted,commit=True)
        self.assertEqual(self.run.read_bytes(),original)
        self.assertFalse((self.checkout/"edge_worker.py.before-s25-20pct").exists())

    def test_refuses_ambiguous_checkout(self):
        extra=self.home/"duplicate"
        extra.mkdir()
        (extra/"edge_worker.py").write_text(
            "MIN_BATTERY_PERCENT=40\ndef phone_ready(): pass")
        (extra/"application_client.py").write_text("class ApplicationClient: pass")
        with patch.object(installer,"phone_check",return_value="SM-S931B"):
            with self.assertRaises(installer.Refused):
                installer.apply_update(prefix=self.prefix,home=self.home,
                                       urlopen=self.fetch,commit=True)
        self.assertEqual(len(self.restarts),0)

    def test_conflicting_profile_never_overwritten(self):
        self.run.write_text(self.run.read_text()+"\nexport OCI_EDGE_POWER_PROFILE=s22-edge\n")
        with patch.object(installer,"phone_check",return_value="SM-S931B"):
            with self.assertRaises(installer.Refused):
                installer.apply_update(prefix=self.prefix,home=self.home,
                                       urlopen=self.fetch,commit=True)
        self.assertEqual(len(self.restarts),0)

    def test_reject_s22_device(self):
        class Result:
            returncode=0
            stdout="SM-S908B"
        with patch.object(installer.os,"geteuid",return_value=11000):
            with self.assertRaises(installer.Refused):
                installer.phone_check(
                    prefix="/data/data/com.termux/files/usr",
                    getprop=lambda *args,**kwargs:Result())

    def test_secure_existing_service_launcher_required(self):
        self.run.unlink()
        self.run.symlink_to(self.root/"untrusted")
        with patch.object(installer,"phone_check",return_value="SM-S931B"):
            with self.assertRaises(installer.Refused):
                installer.apply_update(prefix=self.prefix,home=self.home,
                                       urlopen=self.fetch,commit=True)
        self.assertEqual(self.restarts,[])


if __name__=="__main__":
    unittest.main()
