import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_mod  # noqa: E402


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class AssembleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.main_dll = self.root / "build" / "main.dll"
        touch(self.main_dll)
        touch(self.main_dll.with_suffix(".pdb"), "pdb")
        self.runtime = self.root / "runtime"
        for n in ("python312.dll", "python312.zip", "vcruntime140.dll", "python312._pth", "python3.dll"):
            touch(self.runtime / n)
        self.scripts = self.root / "scripts"
        touch(self.scripts / "main.py", "print('hi')")
        touch(self.scripts / "pkg" / "util.py")

    def tearDown(self):
        self._tmp.cleanup()

    def assemble(self, **kwargs):
        # the default game_processes is empty, so these tests never look at the real process list
        return package_mod.assemble(self.main_dll, self.runtime, self.scripts, self.root / "dist", "Mod", retry_delay=0, **kwargs)

    def test_layout(self):
        mod = self.assemble()
        self.assertTrue((mod / "enabled.txt").is_file())
        self.assertEqual((mod / "python_scripts" / "main.py").read_text(), "print('hi')")
        self.assertTrue((mod / "python_scripts" / "pkg" / "util.py").is_file())
        for n in ("main.dll", "main.pdb", "python312.dll", "python312.zip", "vcruntime140.dll"):
            self.assertTrue((mod / "dlls" / n).is_file(), n)

    def test_library_packages_are_shipped_next_to_main_py(self):
        library = self.root / "lib" / "mygame"
        (library / "sub").mkdir(parents=True)
        (library / "__init__.py").write_text("")
        (library / "sub" / "chat.py").write_text("x = 1")
        mod = self.assemble(libraries=[library, self.root / "does_not_exist"])
        self.assertTrue((mod / "python_scripts" / "mygame" / "sub" / "chat.py").exists())
        self.assertTrue((mod / "python_scripts" / "mygame" / "__init__.py").exists())

    def test_a_locked_folder_is_updated_in_place(self):
        first = self.assemble()
        (self.scripts / "main.py").write_text("print('v2')")
        stale = first / "python_scripts" / "stale.py"
        stale.write_text("old")
        real_replace = package_mod.os.replace

        def refuse(source, target):
            if Path(source) == first:        # the folder cannot be renamed (an editor has it open)
                raise PermissionError(13, "denied")
            return real_replace(source, target)

        with mock.patch.object(package_mod.os, "replace", side_effect=refuse):
            again = self.assemble()
        self.assertEqual(again, first)
        self.assertEqual((again / "python_scripts" / "main.py").read_text(), "print('v2')")
        self.assertFalse(stale.exists())                                   # files that no longer belong are removed
        self.assertTrue((again / "dlls" / "main.dll").exists())
        self.assertFalse((self.root / "dist" / ".Mod.new").exists())       # no staging folder left behind

    def test_a_folder_that_becomes_locked_while_swapping_is_updated_in_place(self):
        first = self.assemble()
        (self.scripts / "main.py").write_text("print('v3')")
        real_replace = package_mod.os.replace

        def refuse_the_second_rename(source, target):
            if Path(target) == first and Path(source).name == ".Mod.new":      # moving the new folder into place fails
                raise PermissionError(13, "denied")
            return real_replace(source, target)

        with mock.patch.object(package_mod.os, "replace", side_effect=refuse_the_second_rename):
            again = self.assemble()
        self.assertEqual((again / "python_scripts" / "main.py").read_text(), "print('v3')")
        self.assertTrue((again / "dlls" / "main.dll").exists())
        self.assertFalse((self.root / "dist" / ".Mod.new").exists())
        self.assertFalse((self.root / "dist" / ".Mod.old").exists())

    def test_skips_pth_and_stub_dll(self):
        mod = self.assemble()
        self.assertFalse((mod / "dlls" / "python312._pth").exists())
        self.assertFalse((mod / "dlls" / "python3.dll").exists())

    def test_no_scripts_dir_and_no_executables(self):
        for n in ("python.exe", "pythonw.exe", "python.cat"):
            touch(self.runtime / n)
        mod = self.assemble()
        self.assertFalse((mod / "scripts").exists())
        for n in ("python.exe", "pythonw.exe", "python.cat"):
            self.assertFalse((mod / "dlls" / n).exists(), n)

    def test_rerun_replaces_old_output(self):
        mod = self.assemble()
        touch(mod / "dlls" / "stale.dll")
        mod = self.assemble()
        self.assertFalse((mod / "dlls" / "stale.dll").exists())

    def test_missing_inputs_raise(self):
        self.main_dll.unlink()
        with self.assertRaises(FileNotFoundError):
            self.assemble()

    def test_without_any_script_a_starter_main_is_written(self):
        import shutil
        shutil.rmtree(self.scripts)
        mod = self.assemble()
        self.assertEqual([p.name for p in (mod / "python_scripts").iterdir()], ["main.py"])
        text = (mod / "python_scripts" / "main.py").read_text()
        self.assertEqual(text, package_mod.STARTER_MAIN)
        compile(text, "main.py", "exec")

    def test_scripts_written_into_the_installed_folder_survive_repackaging(self):
        import shutil
        shutil.rmtree(self.scripts)
        mod = self.assemble()
        touch(mod / "python_scripts" / "main.py", "print('mine')")
        touch(mod / "python_scripts" / "helpers" / "doors.py", "X = 1")
        again = self.assemble()
        self.assertEqual((again / "python_scripts" / "main.py").read_text(), "print('mine')")
        self.assertEqual((again / "python_scripts" / "helpers" / "doors.py").read_text(), "X = 1")

    def test_a_scripts_folder_without_main_gets_the_starter_and_keeps_its_files(self):
        (self.scripts / "main.py").unlink()
        mod = self.assemble()
        self.assertEqual((mod / "python_scripts" / "main.py").read_text(), package_mod.STARTER_MAIN)
        self.assertTrue((mod / "python_scripts" / "pkg" / "util.py").is_file())

    def test_runtime_without_python_dll_raises(self):
        (self.runtime / "python312.dll").unlink()
        with self.assertRaises(FileNotFoundError):
            self.assemble()


class SwapAndRetryTest(AssembleTest):
    def test_a_failed_copy_leaves_the_previous_folder_intact(self):
        old = self.assemble()
        touch(old / "dlls" / "marker.txt", "old")

        def failing(source, target):
            if Path(target).name == "python312.dll":
                raise OSError(5, "Input/output error")
            return shutil.copy2(source, target)

        with self.assertRaises(OSError):
            self.assemble(copy=failing, attempts=2)
        self.assertEqual((old / "dlls" / "marker.txt").read_text(), "old")  # the old folder is untouched
        self.assertTrue((old / "dlls" / "main.dll").is_file())
        dist = self.root / "dist"
        self.assertEqual(sorted(p.name for p in dist.iterdir()), ["Mod"])  # no half-built staging folder left behind

    def test_transient_copy_errors_are_retried(self):
        calls = {"n": 0}

        def flaky(source, target):
            if Path(target).name == "main.dll" and calls["n"] < 2:
                calls["n"] += 1
                raise OSError(5, "Input/output error")
            return shutil.copy2(source, target)

        mod = self.assemble(copy=flaky, attempts=3)
        self.assertEqual(calls["n"], 2)
        self.assertTrue((mod / "dlls" / "main.dll").is_file())

    def test_a_persistent_error_gives_up_after_the_attempts(self):
        calls = {"n": 0}

        def broken(source, target):
            calls["n"] += 1
            raise OSError(5, "Input/output error")

        with self.assertRaises(OSError):
            self.assemble(copy=broken, attempts=4)
        self.assertEqual(calls["n"], 4)

    def test_stale_staging_folders_from_an_interrupted_run_are_cleaned(self):
        dist = self.root / "dist"
        touch(dist / ".Mod.new" / "junk.txt")
        touch(dist / ".Mod.old" / "junk.txt")
        self.assemble()
        self.assertEqual(sorted(p.name for p in dist.iterdir()), ["Mod"])


class RunningGameTest(AssembleTest):
    def test_refuses_while_the_game_runs_and_touches_nothing(self):
        old = self.assemble()
        touch(old / "dlls" / "marker.txt", "old")
        with self.assertRaises(package_mod.GameRunningError) as raised:
            self.assemble(game_processes=("Game-Win64-Shipping.exe",), process_lister=lambda: {"explorer.exe", "game-win64-shipping.exe"})
        self.assertIn("Game-Win64-Shipping.exe", str(raised.exception))
        self.assertEqual((old / "dlls" / "marker.txt").read_text(), "old")

    def test_packages_when_the_game_is_not_running(self):
        mod = self.assemble(game_processes=("Game-Win64-Shipping.exe",), process_lister=lambda: {"explorer.exe"})
        self.assertTrue((mod / "enabled.txt").is_file())

    def test_image_names_match_case_insensitively(self):
        self.assertEqual(package_mod.running_game(["Foo.EXE"], lambda: {"foo.exe"}), "Foo.EXE")
        self.assertIsNone(package_mod.running_game(["Foo.exe"], lambda: {"bar.exe"}))
        self.assertIsNone(package_mod.running_game([], lambda: {"anything"}))

    def test_a_locked_main_dll_blocks_packaging(self):
        self.assemble()
        with mock.patch.object(package_mod, "file_is_locked", return_value=True):
            with self.assertRaises(package_mod.GameRunningError):
                self.assemble()

    def test_file_is_locked_is_false_for_a_normal_or_missing_file(self):
        self.assertFalse(package_mod.file_is_locked(self.root / "nothing.dll"))
        self.assertFalse(package_mod.file_is_locked(self.main_dll))

    def test_command_line_refuses_with_exit_code_2_and_force_skips(self):
        args = ["--main-dll", str(self.main_dll), "--runtime-dir", str(self.runtime), "--scripts-dir", str(self.scripts),
                "--out-dir", str(self.root / "dist"), "--name", "Mod", "--game-process", "G.exe"]
        with mock.patch.object(package_mod, "running_processes", return_value={"g.exe"}), mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(package_mod.main(args), 2)
        self.assertIn("refusing to package", err.getvalue())
        self.assertFalse((self.root / "dist" / "Mod").exists())
        with mock.patch.object(package_mod, "running_processes", return_value={"g.exe"}), mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(package_mod.main(args + ["--force"]), 0)
        self.assertTrue((self.root / "dist" / "Mod" / "enabled.txt").is_file())


if __name__ == "__main__":
    unittest.main()
