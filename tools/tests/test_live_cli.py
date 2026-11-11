"""tools/live.py against a bridge that runs in this process: the generated main.py is executed with a fake ue4ss whose scheduler
is ticked by a thread, as the engine would."""
import contextlib
import io
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live  # noqa: E402
from live_client import LiveClient, LiveClientError  # noqa: E402


class LiveCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.mod = Path(self._tmp.name) / "PythonScripting"
        (self.mod / "python_scripts").mkdir(parents=True)
        self.session = self.mod / "live"
        self.main_py = self.mod / "python_scripts" / "main.py"
        self.said = []
        self.stop_ticking = threading.Event()
        self.saved = sys.modules.get("ue4ss")

    def tearDown(self):
        self.stop_ticking.set()
        if self.saved is not None:
            sys.modules["ue4ss"] = self.saved
        else:
            sys.modules.pop("ue4ss", None)
        self._tmp.cleanup()

    def start_game(self):
        """Run the installed main.py like the game would: on a 'game thread' that ticks the scheduler."""
        schedule, lock = [], threading.Lock()
        fake = types.ModuleType("ue4ss")
        fake.execute_with_delay = lambda delay, fn: schedule.append((time.monotonic() + delay / 1000.0, fn))
        fake.reload = lambda: None
        fake.find_first_of = lambda name: None
        fake.find_all_of = lambda name: ["a", "b", "c"]
        fake.UEError = type("UEError", (Exception,), {})
        sys.modules["ue4ss"] = fake
        source = self.main_py.read_text(encoding="utf-8")

        def game_thread():
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(source, "main.py", "exec"), {"__name__": "__main__"})
            while not self.stop_ticking.is_set():
                now = time.monotonic()
                with lock:
                    due = [entry for entry in schedule if entry[0] <= now]
                    for entry in due:
                        schedule.remove(entry)
                for _, fn in due:
                    fn()
                time.sleep(0.01)

        threading.Thread(target=game_thread, daemon=True).start()
        client = LiveClient(self.session, poll=0.02)
        deadline = time.monotonic() + 5
        while not client.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(client.is_alive(), "the bridge wrote no heartbeat")
        return client

    def run_code(self, client, code):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code_exit = live.execute(client, code, 10.0)
        return code_exit, out.getvalue(), err.getvalue()

    def test_install_keeps_the_users_script_and_uninstall_puts_it_back(self):
        self.main_py.write_text("print('mine')")
        live.install(self.mod, self.session, say=self.said.append)
        self.assertTrue(self.main_py.read_text().startswith(live.BRIDGE_MARK))
        self.assertEqual((self.mod / "python_scripts" / live.BACKUP_NAME).read_text(), "print('mine')")
        live.install(self.mod, self.session, say=self.said.append)                      # again: the backup is not overwritten by the bridge
        self.assertEqual((self.mod / "python_scripts" / live.BACKUP_NAME).read_text(), "print('mine')")
        live.uninstall(self.mod, say=self.said.append)
        self.assertEqual(self.main_py.read_text(), "print('mine')")
        self.assertFalse((self.mod / "python_scripts" / live.BACKUP_NAME).exists())
        with self.assertRaisesRegex(LiveClientError, "not the bridge"):
            live.uninstall(self.mod, say=self.said.append)

    def test_install_needs_a_packaged_mod(self):
        with self.assertRaisesRegex(LiveClientError, "package the mod first"):
            live.install(self.mod.parent / "nowhere", self.session, say=self.said.append)

    def test_cli_uses_a_foreign_mod_and_session_folder(self):
        self.assertEqual(live.main(["--mod-dir", str(self.mod), "--session-dir", str(self.session), "install"]), 0)
        self.assertTrue(self.main_py.read_text(encoding="utf-8").startswith(live.BRIDGE_MARK))
        self.assertTrue((self.session / "inbox").is_dir())

    def test_code_runs_in_the_game_and_output_values_and_state_come_back(self):
        live.install(self.mod, self.session, poll_ms=50, say=self.said.append)
        client = self.start_game()
        self.assertEqual(self.run_code(client, "print('hello from the game')"), (0, "hello from the game\n", ""))
        self.assertEqual(self.run_code(client, "len(ue4ss.find_all_of('Actor'))"), (0, "result: 3\n", ""))      # an expression shows its value
        self.assertEqual(self.run_code(client, "x = 21"), (0, "", ""))                                         # and a statement shows nothing
        self.assertEqual(self.run_code(client, "x * 2"), (0, "result: 42\n", ""))                              # variables stay between requests
        code_exit, out, err = self.run_code(client, "1 / 0")
        self.assertEqual(code_exit, 1)
        self.assertIn("ZeroDivisionError", err)
        self.assertEqual(self.run_code(client, "x"), (0, "result: 21\n", ""))                                  # the session survives an error

    def test_without_a_bridge_exec_says_what_to_do(self):
        code_exit, out, err = self.run_code(LiveClient(self.session), "1")
        self.assertEqual(code_exit, 1)
        self.assertIn("live.py install", err)

    def test_paths_are_given_to_the_game_as_windows_paths(self):
        if sys.platform != "win32":
            self.assertEqual(live.game_path(Path("/mnt/p/code/mod/live/inbox")), "P:\\code\\mod\\live\\inbox")
            self.assertEqual(live.game_path(Path("/home/me/x")), "/home/me/x")

    def test_an_expression_is_shown_and_other_code_clears_the_old_value(self):
        self.assertEqual(live.prepare(" 1 + 1 "), "result = (1 + 1)")
        self.assertEqual(live.prepare("x = 1"), "result = None\nx = 1")
        self.assertEqual(live.prepare("for i in range(2):\n    print(i)"), "result = None\nfor i in range(2):\n    print(i)")


if __name__ == "__main__":
    unittest.main()
