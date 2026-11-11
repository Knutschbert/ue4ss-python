import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import testgame  # noqa: E402


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class FakeGame:
    """A game that 'prints' given log lines per poll; the clock advances with every sleep."""

    def __init__(self, game_dir: Path, script=(), exits_after=None, dump_after=None):
        self.game_dir, self.exe_name = game_dir, "PyTestGame-Win64-Shipping.exe"
        self.script, self.exits_after, self.dump_after = list(script), exits_after, dump_after
        self.started_with, self.stopped, self.ticks, self.main_py_at_start = None, 0, 0, None
        self.now = 0.0

    @property
    def log(self):
        return self.game_dir / "UE4SS.log"

    def start(self, args, env=None):
        self.started_with = (list(args), env)
        self.main_py_at_start = (self.game_dir.parent / "repo" / "dist" / "PythonScripting" / "python_scripts" / "main.py").read_text(encoding="utf-8")

    def sleep(self, seconds):
        self.now += seconds
        self.ticks += 1
        if self.script:
            with open(self.log, "a", encoding="utf-8") as stream:
                stream.write("[12:00:00] [Python] " + self.script.pop(0) + "\n")
        if self.dump_after is not None and self.ticks >= self.dump_after:
            touch(self.game_dir / "crash_1.dmp")

    def clock(self):
        return self.now

    def alive(self):
        return self.exits_after is None or self.ticks < self.exits_after

    def stop(self):
        self.stopped += 1
        return "ended"

    def crash_dumps(self):
        return {dump.name for dump in self.game_dir.glob("crash_*.dmp")}


class RunExampleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.game_dir, self.root, self.examples = base / "game", base / "repo", base / "examples"
        touch(self.game_dir / "PyTestGame-Win64-Shipping.exe")
        touch(self.game_dir / "UE4SS.dll")
        self.main_py = touch(self.root / "dist" / "PythonScripting" / "python_scripts" / "main.py", "print('the developer own script')")
        touch(self.examples / "arrays_main.py", "print('PASS a')\nprint('arrays finished')")
        self.entry = {"file": "arrays_main.py", "marker": "arrays finished", "kind": "test", "targets": ["testgame"], "min_pass": {"testgame": 2}}
        self.echoed = []

    def tearDown(self):
        self._tmp.cleanup()

    def run_with(self, game, entry=None, **kwargs):
        return testgame.run_example(entry or self.entry, game=game, root=self.root, examples=self.examples, clock=game.clock, sleep=game.sleep,
                                    echo=self.echoed.append, **kwargs)

    def test_a_passing_script_is_counted_and_the_game_is_ended_once(self):
        game = FakeGame(self.game_dir, ["PASS one", "two: PASS (detail)", "x: SKIP (no such class)", "arrays finished PASS=2 FAIL=0"])
        result = self.run_with(game)
        self.assertEqual((result.passed, result.failed, result.skipped, result.finished, result.problem), (2, 0, 1, True, ""))
        self.assertEqual(testgame.verdict(self.entry, result), "")
        self.assertEqual(game.stopped, 1)
        self.assertEqual(game.started_with[0], testgame.HEADLESS_ARGS)

    def test_the_developers_main_py_is_put_back_and_the_script_ran_through_the_wrapper(self):
        game = FakeGame(self.game_dir, ["arrays finished"])
        self.run_with(game)
        self.assertEqual(self.main_py.read_text(), "print('the developer own script')")
        self.assertTrue(game.main_py_at_start.startswith(testgame.TEST_COPY_MARK))
        self.assertIn("PyTestActor", game.main_py_at_start)                  # waits for the test world
        compile(game.main_py_at_start, "main.py", "exec")

    def test_a_startup_script_is_copied_as_it_is(self):
        game = FakeGame(self.game_dir, ["arrays finished"])
        self.run_with(game, dict(self.entry, startup=True))
        self.assertEqual(game.main_py_at_start, testgame.TEST_COPY_MARK + "\nprint('PASS a')\nprint('arrays finished')")

    def test_an_old_log_is_not_read(self):
        touch(self.game_dir / "UE4SS.log", "[x] [Python] FAIL from an earlier run\n[x] [Python] arrays finished\n")
        result = self.run_with(FakeGame(self.game_dir, ["PASS one", "PASS two", "arrays finished"]))
        self.assertEqual((result.passed, result.failed), (2, 0))

    def test_a_traceback_ends_the_wait_and_fails_unless_tolerated(self):
        game = FakeGame(self.game_dir, ["PASS one", "Traceback (most recent call last):", "PASS never reached", "arrays finished"])
        result = self.run_with(game)
        self.assertFalse(result.finished)
        self.assertEqual(testgame.verdict(self.entry, result), "did not print its finish marker")
        tolerant = dict(self.entry, tolerate_tracebacks=True, min_pass={"testgame": 2})
        result = self.run_with(FakeGame(self.game_dir, ["PASS one", "Traceback (most recent call last):", "PASS two", "arrays finished"]), tolerant)
        self.assertEqual(testgame.verdict(tolerant, result), "")

    def test_a_fail_line_and_a_missed_floor_are_reported(self):
        result = self.run_with(FakeGame(self.game_dir, ["PASS one", "FAIL two", "arrays finished"]))
        self.assertEqual(testgame.verdict(self.entry, result), "1 FAIL/traceback line(s)")
        result = self.run_with(FakeGame(self.game_dir, ["PASS one", "arrays finished"]))
        self.assertEqual(testgame.verdict(self.entry, result), "only 1 PASS lines, expected at least 2")

    def test_timeout_crash_dump_and_early_exit_are_problems(self):
        result = self.run_with(FakeGame(self.game_dir, ["PASS one"]), timeout=5)
        self.assertIn("timed out after 5 s", result.problem)
        result = self.run_with(FakeGame(self.game_dir, [], dump_after=3))
        self.assertIn("crashed", result.problem)
        (self.game_dir / "crash_1.dmp").unlink()
        game = FakeGame(self.game_dir, [], exits_after=2)
        result = self.run_with(game)
        self.assertIn("exited on its own", result.problem)
        self.assertEqual(game.stopped, 1)

    def test_a_dump_of_an_earlier_run_is_not_a_crash(self):
        touch(self.game_dir / "crash_old.dmp")
        result = self.run_with(FakeGame(self.game_dir, ["PASS a", "PASS b", "", "", "", "arrays finished"]))
        self.assertEqual(result.problem, "")

    def test_a_script_that_quits_the_game_must_see_it_exit_in_time(self):
        entry = dict(self.entry, expect_exit_within=20, min_pass={"testgame": 0})
        result = self.run_with(FakeGame(self.game_dir, ["arrays finished"], exits_after=6), entry)
        self.assertEqual(result.problem, "")
        self.assertIsNotNone(result.exit_seconds)
        result = self.run_with(FakeGame(self.game_dir, ["arrays finished"]), dict(entry, expect_exit_within=3))
        self.assertIn("hang at exit", result.problem)

    def test_the_environment_of_the_manifest_reaches_the_game(self):
        game = FakeGame(self.game_dir, ["arrays finished"])
        self.run_with(game, dict(self.entry, env={"PYUE_EXECUTOR": "processevent"}))
        self.assertEqual(game.started_with[1], {"PYUE_EXECUTOR": "processevent"})

    def test_a_game_that_is_not_staged_or_not_installed_is_not_started(self):
        (self.game_dir / "UE4SS.dll").unlink()
        game = FakeGame(self.game_dir)
        self.assertIn("testgame.py install", self.run_with(game).problem)
        (self.game_dir / "PyTestGame-Win64-Shipping.exe").unlink()
        self.assertIn("package_testgame.ps1", self.run_with(game).problem)
        self.assertIsNone(game.started_with)


class ManifestAndReportTest(unittest.TestCase):
    def test_the_real_manifest_selects_test_scripts_that_exist(self):
        chosen = testgame.selected(testgame.load_manifest())
        self.assertGreaterEqual(len(chosen), 50)
        for entry in chosen:
            self.assertTrue((testgame.EXAMPLES / entry["file"]).is_file(), entry["file"])
            self.assertTrue(entry["marker"])
        self.assertEqual([e["file"] for e in testgame.selected(testgame.load_manifest(), ["arrays_main", "enums_main.py"])], ["arrays_main.py", "enums_main.py"])
        with self.assertRaisesRegex(testgame.TestGameError, "not in the manifest"):
            testgame.selected(testgame.load_manifest(), ["no_such_main"])

    def test_log_lines_lose_their_prefix_and_other_lines_are_dropped(self):
        text = "[2026] UE4SS started\n[2026] [Python] PASS one\n[2026] [Python]   File \"main.py\", line 3\n"
        self.assertEqual(testgame.python_lines(text), ["PASS one", "  File \"main.py\", line 3"])

    def test_run_all_writes_outputs_and_results_and_counts_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            entries = [{"file": "a_main.py", "marker": "a done", "min_pass": {"testgame": 1}}, {"file": "b_main.py", "marker": "b done"}]

            def runner(entry, **kwargs):
                good = entry["file"] == "a_main.py"
                return testgame.RunResult(entry["file"], started=True, finished=good, passed=1 if good else 0, seconds=1.5, lines=["PASS x", "a done"] if good else ["FAIL y"],
                                          failed=0 if good else 1)

            said = []
            failures = testgame.run_all(entries, runner=runner, say=said.append, runs=base / "runs", results=base / "results.json")
            self.assertEqual(failures, 1)
            self.assertEqual((base / "runs" / "a_main.txt").read_text(), "PASS x\na done\n")
            report = json.loads((base / "results.json").read_text())
            self.assertEqual((report["total"], report["failed"]), (2, 1))
            self.assertEqual([e["ok"] for e in report["examples"]], [True, False])
            self.assertIn("1 of 2 passed, 1 did not: b_main.py", said[-1])


    def test_an_expected_failure_does_not_fail_the_pass_but_an_unexpected_pass_does(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            entries = [{"file": "known_main.py", "marker": "done", "expected_failure": "a known defect"},
                       {"file": "fixed_main.py", "marker": "done", "expected_failure": "was a defect once"},
                       {"file": "fine_main.py", "marker": "done"}]

            def runner(entry, **kwargs):
                bad = entry["file"] == "known_main.py"
                return testgame.RunResult(entry["file"], started=True, finished=True, passed=0 if bad else 1, failed=1 if bad else 0, lines=["FAIL x", "done"] if bad else ["PASS x", "done"])

            said = []
            failures = testgame.run_all(entries, runner=runner, say=said.append, runs=base / "runs", results=base / "results.json")
            self.assertEqual(failures, 1)                                          # only the one that passed unexpectedly
            report = json.loads((base / "results.json").read_text())
            self.assertEqual(report["expected_failures"], 1)
            by_file = {e["file"]: e for e in report["examples"]}
            self.assertTrue(by_file["known_main.py"]["ok"] and by_file["known_main.py"]["expected_failure"])
            self.assertFalse(by_file["fixed_main.py"]["ok"])
            self.assertIn("remove that entry", by_file["fixed_main.py"]["why"])
            self.assertIn("1 of 3 passed, 1 failed as expected (known defects), 1 did not: fixed_main.py", said[-1])

    def test_run_all_passes_a_foreign_examples_folder_to_the_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            seen = {}

            def runner(entry, **kwargs):
                seen.update(kwargs)
                return testgame.RunResult(entry["file"], started=True, finished=True, passed=1, lines=["PASS x", "done"])

            examples = base / "game-package" / "examples"
            testgame.run_all([{"file": "main.py", "marker": "done"}], runner=runner, runs=base / "runs", results=base / "results.json",
                             root=base / "base", examples=examples)
            self.assertEqual((seen["root"], seen["examples"]), (base / "base", examples))


if __name__ == "__main__":
    unittest.main()
