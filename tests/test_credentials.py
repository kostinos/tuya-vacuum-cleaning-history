"""Regression tests for migration independent of a running Home Assistant."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "tuvio_history"
pkg = ModuleType("history_test_package")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
spec = importlib.util.spec_from_file_location("history_test_package.credentials", ROOT / "credentials.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
independent_settings = module.independent_settings


class MigrationTests(unittest.TestCase):
    def test_copies_only_needed_fields_and_detaches(self):
        entries = {
            "cloud": SimpleNamespace(data={"client_id": "id", "client_secret": "secret", "region": "we", "token": "do-not-copy"}),
            "vacuum": SimpleNamespace(data={"device_id": "robot", "local_key": "do-not-copy"}),
        }
        old = {"cloud_entry_id": "cloud", "vacuum_entry_id": "vacuum", "suction_entity_id": "select.power"}
        new = independent_settings(old, entries.get)
        self.assertEqual(new, {"client_id": "id", "client_secret": "secret", "region": "we", "device_id": "robot", "suction_entity_id": "select.power"})
        entries.clear()
        self.assertEqual(independent_settings(new, entries.get), new)
        self.assertIn("cloud_entry_id", old)

    def test_deleted_cloud_keeps_device_for_reauthentication(self):
        entries = {"vacuum": SimpleNamespace(data={"device_id": "robot"})}
        new = independent_settings({"cloud_entry_id": "deleted", "vacuum_entry_id": "vacuum"}, entries.get)
        self.assertEqual(new, {"device_id": "robot", "region": "eu"})

    def test_owned_credentials_take_precedence(self):
        entries = {"cloud": SimpleNamespace(data={"client_id": "old", "client_secret": "old"})}
        new = independent_settings({"cloud_entry_id": "cloud", "client_id": "new", "client_secret": "new"}, entries.get)
        self.assertEqual(new["client_id"], "new")
        self.assertEqual(new["client_secret"], "new")


if __name__ == "__main__":
    unittest.main()
