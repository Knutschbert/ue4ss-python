"""Opt-in managed bootstrap installation and local manifest operations."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import live  # noqa: E402
import managed  # noqa: E402
from ue4ss.live.security import provision_token  # noqa: E402


class ManagedInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.mod = Path(self.temporary.name) / "PythonScripting"
        (self.mod / "python_scripts").mkdir(parents=True)
        self.main = self.mod / "python_scripts" / "main.py"

    def tearDown(self):
        self.temporary.cleanup()

    def test_install_repeat_and_uninstall_preserve_user_main_and_manifest(self):
        self.main.write_text("print('user entry')\n", encoding="utf-8")
        managed.install(self.mod, say=lambda _text: None)
        backup = self.main.with_name(managed.BACKUP_NAME)
        self.assertEqual(backup.read_text(encoding="utf-8"), "print('user entry')\n")
        self.assertTrue(self.main.read_text(encoding="utf-8").startswith(managed.BOOTSTRAP_MARK))
        manifest = self.mod / managed.STATE_DIR / managed.MANIFEST
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), {
            "schema_version": 1,
            "startup_order": [],
            "components": {},
        })
        managed.install(self.mod, say=lambda _text: None)
        self.assertEqual(backup.read_text(encoding="utf-8"), "print('user entry')\n")
        managed.uninstall(self.mod, say=lambda _text: None)
        self.assertEqual(self.main.read_text(encoding="utf-8"), "print('user entry')\n")
        self.assertTrue(manifest.is_file(), "uninstall must preserve managed configuration")

    def test_first_install_without_user_main_uninstalls_without_deleting_state(self):
        managed.install(self.mod, say=lambda _text: None)
        state = self.mod / managed.STATE_DIR / managed.MANIFEST
        assert state.is_file()
        managed.uninstall(self.mod, say=lambda _text: None)
        assert not self.main.exists()
        assert state.is_file()

    def test_dry_run_validates_and_leaves_installation_untouched(self):
        self.main.write_text("print('user entry')\n", encoding="utf-8")
        messages = []
        managed.install(self.mod, say=messages.append, dry_run=True)
        self.assertIn("would install", messages[0])
        self.assertEqual(self.main.read_text(encoding="utf-8"), "print('user entry')\n")
        self.assertFalse((self.mod / managed.STATE_DIR).exists())

    def test_legacy_live_bridge_migration_preserves_both_backup_generations(self):
        live_backup = self.main.with_name(live.BACKUP_NAME)
        live_backup.write_text("print('original user main')\n", encoding="utf-8")
        self.main.write_text(live.BRIDGE_MARK + "\n# legacy bridge\n", encoding="utf-8")
        managed.install(self.mod, say=lambda _text: None)
        managed_backup = self.main.with_name(managed.BACKUP_NAME)
        assert managed_backup.read_text(encoding="utf-8").startswith(live.BRIDGE_MARK)
        assert live_backup.read_text(encoding="utf-8") == "print('original user main')\n"
        managed.uninstall(self.mod, say=lambda _text: None)
        assert self.main.read_text(encoding="utf-8").startswith(live.BRIDGE_MARK)
        live.uninstall(self.mod, say=lambda _text: None)
        assert self.main.read_text(encoding="utf-8") == "print('original user main')\n"

    def test_ambiguous_backup_collision_refuses_without_mutation(self):
        for collision_name in (managed.BACKUP_NAME, live.BACKUP_NAME):
            with self.subTest(collision=collision_name):
                self.main.write_text("print('current user main')\n", encoding="utf-8")
                self.main.with_name(collision_name).write_text("older backup", encoding="utf-8")
                before = self.main.read_bytes()
                with self.assertRaises(managed.ManagedInstallError):
                    managed.install(self.mod, say=lambda _text: None)
                self.assertEqual(self.main.read_bytes(), before)
                self.main.with_name(collision_name).unlink()

    def test_component_enable_disable_and_source_preservation(self):
        managed.install(self.mod, say=lambda _text: None)
        source = Path(self.temporary.name) / "source" / "Demo"
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("def start(ctx): pass\n", encoding="utf-8")
        managed.enable(self.mod, "Demo", source)
        manifest = json.loads(
            (self.mod / managed.STATE_DIR / managed.MANIFEST).read_text(encoding="utf-8")
        )
        assert manifest["startup_order"] == ["Demo"]
        assert manifest["components"]["Demo"]["enabled"] is True
        managed.disable(self.mod, "Demo")
        manifest = json.loads(
            (self.mod / managed.STATE_DIR / managed.MANIFEST).read_text(encoding="utf-8")
        )
        assert manifest["components"]["Demo"]["enabled"] is False
        assert (
            self.mod / managed.STATE_DIR / "components" / "Demo" / "__init__.py"
        ).is_file()

    def test_component_enable_dry_run_does_not_copy_or_change_manifest(self):
        managed.install(self.mod, say=lambda _text: None)
        manifest_path = self.mod / managed.STATE_DIR / managed.MANIFEST
        before = manifest_path.read_bytes()
        source = Path(self.temporary.name) / "source" / "Demo"
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("def start(ctx): pass\n", encoding="utf-8")
        managed.enable(self.mod, "Demo", source, dry_run=True)
        self.assertEqual(manifest_path.read_bytes(), before)
        self.assertFalse((self.mod / managed.STATE_DIR / "components" / "Demo").exists())

    def test_managed_live_install_selects_transport_and_disable_preserves_peers_and_main(self):
        managed.install(self.mod, say=lambda _text: None)
        token_file = Path(self.temporary.name) / "secrets" / "live.token"
        provision_token(token_file)
        other = Path(self.temporary.name) / "source" / "Other"
        other.mkdir(parents=True)
        (other / "__init__.py").write_text("def start(ctx): pass\n", encoding="utf-8")
        managed.enable(self.mod, "Other", other)
        main_before = self.main.read_bytes()

        managed.configure_live(
            self.mod,
            transport="both",
            file_config={
                "inbox": "P:\\game\\live\\inbox",
                "outbox": "P:\\game\\live\\outbox",
                "heartbeat": "P:\\game\\live\\alive",
                "poll_ms": 250,
            },
            socket_config={
                "host": "127.0.0.1",
                "port": 9000,
                "token_file": str(token_file),
            },
        )
        manifest_path = self.mod / managed.STATE_DIR / managed.MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        live_item = manifest["components"][managed.LIVE_NAME]
        self.assertTrue(live_item["enabled"])
        self.assertEqual(live_item["config"]["transport"], "both")
        self.assertEqual(live_item["config"]["socket"]["port"], 9000)
        self.assertTrue((self.mod / managed.STATE_DIR / live_item["path"] / "__init__.py").is_file())
        self.assertEqual(manifest["components"]["Other"]["enabled"], True)
        self.assertEqual(self.main.read_bytes(), main_before)

        managed.configure_live(
            self.mod,
            transport="file",
            file_config={
                "inbox": "P:\\game\\live\\inbox",
                "outbox": "P:\\game\\live\\outbox",
                "heartbeat": "P:\\game\\live\\alive",
                "poll_ms": 250,
            },
            enabled=False,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["components"][managed.LIVE_NAME]["enabled"])
        self.assertTrue(manifest["components"]["Other"]["enabled"])
        self.assertEqual(self.main.read_bytes(), main_before)

    def test_live_enable_dry_run_does_not_create_component_or_manifest_entry(self):
        managed.install(self.mod, say=lambda _text: None)
        managed.configure_live(
            self.mod,
            transport="file",
            file_config={
                "inbox": "inbox",
                "outbox": "outbox",
                "heartbeat": "alive",
                "poll_ms": 250,
            },
            dry_run=True,
        )
        manifest = json.loads(
            (self.mod / managed.STATE_DIR / managed.MANIFEST).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["components"], {})
        self.assertFalse((self.mod / managed.STATE_DIR / "components" / managed.LIVE_NAME).exists())
