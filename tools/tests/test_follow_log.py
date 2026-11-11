import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import follow_log  # noqa: E402


class FollowLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "UE4SS.log"

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_python_lines_are_wanted_and_the_marker_is_removed(self):
        line = "[2026-01-01 10:00:00] [Python] hello"
        self.assertTrue(follow_log.wanted(line))
        self.assertFalse(follow_log.wanted("[2026-01-01 10:00:00] Scan finished in 204 ms"))
        self.assertTrue(follow_log.wanted("anything", everything=True))
        self.assertEqual(follow_log.clean(line + "\r\n"), "hello")
        self.assertEqual(follow_log.clean("[Python]   File \"main.py\", line 3"), "  File \"main.py\", line 3")   # traceback indentation stays
        self.assertEqual(follow_log.clean("no marker"), "no marker")

    def test_new_complete_lines_are_returned_once_and_a_half_line_waits(self):
        follower = follow_log.Follower(self.log)
        self.assertEqual(follower.poll(), [])                         # no file yet
        self.log.write_bytes(b"one\ntwo\nthr")
        self.assertEqual(follower.poll(), ["one", "two"])
        self.assertEqual(follower.poll(), [])
        with open(self.log, "ab") as stream:
            stream.write(b"ee\nfour\n")
        self.assertEqual(follower.poll(), ["three", "four"])

    def test_a_new_shorter_log_is_read_from_the_top(self):
        follower = follow_log.Follower(self.log)
        self.log.write_bytes(b"a long first run of the game\nsecond line\n")
        follower.poll()
        self.log.write_bytes(b"new run\n")                            # the game was started again
        self.assertEqual(follower.poll(), ["new run"])

    def test_once_prints_the_python_lines_that_exist(self):
        import contextlib
        import io
        self.log.write_text("[x] UE4SS started\n[x] [Python] first\n[x] [Python] second\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(follow_log.main(["--log", str(self.log), "--once"]), 0)
        self.assertEqual(out.getvalue(), "first\nsecond\n")


if __name__ == "__main__":
    unittest.main()
