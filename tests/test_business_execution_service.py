"""Non-root, idempotent business reconciliation timer installation tests."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

FILE=Path(__file__).resolve().parents[1]/"scripts"/"install-business-execution.py"
spec=importlib.util.spec_from_file_location("business_service_installer",FILE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class InstallTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name)
        self.repo=self.home/"open-ci-hub"
        self.repo.mkdir()
        self.calls=[]
        def run(*argv,**kw):
            self.calls.append(argv)
            if argv[-2:]==("branch","--show-current"):
                return "main"
            if argv[-2:]==("status","--porcelain"):
                return ""
            if argv[-2:]==("rev-parse","HEAD"):
                return "f"*40
            if argv[-2:]==("rev-parse","origin/main"):
                return "f"*40
            if argv[-1]=="is-enabled":
                return "enabled"
            if argv[:3]==("systemctl","--user","show"):
                return "Result=success\nExecMainStatus=0"
            return ""
        self.run=run

    def test_no_confirmation_no_units(self):
        with self.assertRaises(PermissionError):
            mod.install(root=self.repo,home=self.home,confirm=False)
        self.assertFalse((self.home/".config").exists())

    def test_units_are_bounded_shadow_and_never_delete_other_tasks(self):
        unit=mod.unit_text(self.repo)
        timer=mod.timer_text()
        for phrase in ("Type=oneshot","NoNewPrivileges=true","PrivateTmp=true",
                       "MemoryMax=256M","CPUQuota=25%","Nice=17",
                       "business_execution.py run"):
            self.assertIn(phrase,unit)
        self.assertNotIn("curl",unit)
        self.assertNotIn("git push",unit)
        self.assertNotIn("mail",unit)
        self.assertIn("OnCalendar=*-*-* *:12,42:00",timer)

    def test_idempotent_install_same_units_only(self):
        with patch.object(mod.os,"geteuid",return_value=1000),patch.object(
                mod,"checked",side_effect=self.run):
            a=mod.install(root=self.repo,home=self.home,confirm=True)
            b=mod.install(root=self.repo,home=self.home,confirm=True)
        self.assertTrue(a["installed"])
        self.assertTrue(b["installed"])
        self.assertEqual(a["existing_chatgpt_tasks"],"preserved")
        unit=self.home/".config/systemd/user/open-ci-hub-business-execution.service"
        self.assertEqual(unit.read_text(),mod.unit_text(self.repo))

    def test_conflicting_preexisting_unit_not_overwritten(self):
        path=self.home/".config/systemd/user/open-ci-hub-business-execution.timer"
        path.parent.mkdir(parents=True)
        path.write_text("legacy unit - preserve")
        with patch.object(mod.os,"geteuid",return_value=1000),patch.object(
                mod,"checked",side_effect=self.run):
            with self.assertRaises(RuntimeError):
                mod.install(root=self.repo,home=self.home,confirm=True)
        self.assertEqual(path.read_text(),"legacy unit - preserve")

    def test_dirty_repo_refused_before_units(self):
        def dirty(*argv,**kw):
            if argv[-2:]==("status","--porcelain"):
                return " M code.py"
            return self.run(*argv,**kw)
        with patch.object(mod.os,"geteuid",return_value=1000),patch.object(
                mod,"checked",side_effect=dirty):
            with self.assertRaises(RuntimeError):
                mod.install(root=self.repo,home=self.home,confirm=True)
        self.assertFalse((self.home/".config").exists())


if __name__=="__main__":
    unittest.main()
