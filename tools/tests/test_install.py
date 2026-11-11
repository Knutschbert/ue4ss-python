import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install  # noqa: E402
import local_config  # noqa: E402


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class InstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "repo"
        self.game = base / "Games" / "MyGame"
        self.win64 = self.game / "MyGame" / "Binaries" / "Win64"
        touch(self.win64 / "MyGame-Win64-Shipping.exe")
        bin_dir = self.root / "build" / "Game__Shipping__Win64" / "bin"
        touch(bin_dir / "UE4SS.dll", "new ue4ss")
        touch(bin_dir / "dwmapi.dll", "new proxy")
        touch(self.root.parent / "RE-UE4SS" / "assets" / "UE4SS-settings.ini", "[General]")
        self.said, self.built, self.packaged = [], [], []

        def package():
            self.packaged.append(True)
            touch(self.root / "dist" / "PythonScripting" / "dlls" / "main.dll", "mod")
            touch(self.root / "dist" / "PythonScripting" / "python_scripts" / "main.py", "print('starter')")

        self.package = package

    def tearDown(self):
        self._tmp.cleanup()

    def installer(self, **kwargs):
        options = dict(say=self.said.append, link=lambda link, target: os.symlink(target, link, target_is_directory=True),
                       build=lambda target, config, root: self.built.append(target), package=self.package, running=lambda names: None)
        options.update(kwargs)
        return install.Installer(self.root, **options)

    def run_install(self, **kwargs):
        self.installer().run(self.win64, "MyGame-Win64-Shipping.exe", **kwargs)

    # --- finding the game ---
    def test_the_game_is_found_from_its_folder_its_binaries_folder_or_its_executable(self):
        expected = (self.win64, "MyGame-Win64-Shipping.exe")
        self.assertEqual(install.find_game(self.game), expected)
        self.assertEqual(install.find_game(self.win64), expected)
        self.assertEqual(install.find_game(self.win64 / "MyGame-Win64-Shipping.exe"), expected)

    def test_a_folder_without_a_game_or_with_two_is_refused_with_a_reason(self):
        with self.assertRaisesRegex(install.InstallError, "no \\*-Win64-Shipping.exe"):
            install.find_game(self.game.parent.parent)
        touch(self.game / "Other" / "Binaries" / "Win64" / "Other-Win64-Shipping.exe")
        with self.assertRaisesRegex(install.InstallError, "more than one"):
            install.find_game(self.game)
        with self.assertRaisesRegex(install.InstallError, "does not exist"):
            install.find_game(self.game / "nowhere")

    def test_an_executable_given_by_name_is_used_even_when_the_folder_holds_several(self):
        touch(self.win64 / "_MyGame-Win64-Shipping.exe")              # a renamed copy next to the real one
        with self.assertRaisesRegex(install.InstallError, "pass the one to use"):
            install.find_game(self.win64)
        self.assertEqual(install.find_game(self.win64 / "MyGame-Win64-Shipping.exe"), (self.win64, "MyGame-Win64-Shipping.exe"))
        with self.assertRaisesRegex(install.InstallError, "not an executable"):
            install.find_game(touch(self.win64 / "notes.txt"))

    # --- the run ---
    def test_a_first_install_builds_packages_copies_ue4ss_and_links_the_mod(self):
        self.run_install()
        self.assertEqual(self.built, ["UE4SS", "proxy", "PythonScripting"])
        self.assertEqual(len(self.packaged), 1)
        self.assertEqual((self.win64 / "UE4SS.dll").read_text(), "new ue4ss")
        self.assertEqual((self.win64 / "dwmapi.dll").read_text(), "new proxy")
        self.assertEqual((self.win64 / "UE4SS-settings.ini").read_text(), "[General]")       # there was none: the default one
        mod = self.win64 / "Mods" / "PythonScripting"
        self.assertTrue(install.is_link(mod))
        self.assertEqual((mod / "python_scripts" / "main.py").read_text(), "print('starter')")

    def test_the_games_own_files_are_kept_once_and_its_settings_are_left_alone(self):
        touch(self.win64 / "UE4SS.dll", "stock ue4ss")
        touch(self.win64 / "UE4SS-settings.ini", "[Mine]")
        touch(self.win64 / "Mods" / "mods.txt", "Other : 1")
        self.run_install()
        self.assertEqual((self.win64 / "UE4SS.dll.before-ue4ss-python").read_text(), "stock ue4ss")
        self.assertEqual((self.win64 / "UE4SS-settings.ini").read_text(), "[Mine]")
        self.assertEqual((self.win64 / "Mods" / "mods.txt").read_text(), "Other : 1")
        touch(self.root / "build" / "Game__Shipping__Win64" / "bin" / "UE4SS.dll", "newer ue4ss")
        self.run_install()
        self.assertEqual((self.win64 / "UE4SS.dll").read_text(), "newer ue4ss")
        self.assertEqual((self.win64 / "UE4SS.dll.before-ue4ss-python").read_text(), "stock ue4ss")   # the first backup stays

    def test_a_game_file_that_is_a_link_is_never_written_through(self):
        # the game's UE4SS.dll can be a link into another build folder: installing must not overwrite that other file
        elsewhere = touch(self.root.parent / "other-build" / "UE4SS.dll", "the other project's build")
        os.symlink(elsewhere, self.win64 / "UE4SS.dll")
        self.run_install()
        self.assertEqual(elsewhere.read_text(), "the other project's build")
        self.assertFalse(install.is_link(self.win64 / "UE4SS.dll"))
        self.assertEqual((self.win64 / "UE4SS.dll").read_text(), "new ue4ss")
        self.assertTrue(install.is_link(self.win64 / "UE4SS.dll.before-ue4ss-python"))          # the link itself is kept
        self.assertEqual((self.win64 / "UE4SS.dll.before-ue4ss-python").read_text(), "the other project's build")

    def test_a_link_with_the_same_content_is_still_replaced_by_a_real_file(self):
        os.symlink(self.root / "build" / "Game__Shipping__Win64" / "bin" / "UE4SS.dll", self.win64 / "UE4SS.dll")
        self.run_install()
        self.assertFalse(install.is_link(self.win64 / "UE4SS.dll"))
        self.assertEqual((self.root / "build" / "Game__Shipping__Win64" / "bin" / "UE4SS.dll").read_text(), "new ue4ss")

    def test_a_dry_run_does_not_move_a_link(self):
        elsewhere = touch(self.root.parent / "other-build" / "UE4SS.dll", "other")
        os.symlink(elsewhere, self.win64 / "UE4SS.dll")
        self.installer(dry_run=True).run(self.win64, "MyGame-Win64-Shipping.exe")
        self.assertTrue(install.is_link(self.win64 / "UE4SS.dll"))
        self.assertTrue(any("is a link" in line for line in self.said))

    def test_a_second_install_changes_nothing_that_is_up_to_date(self):
        self.run_install()
        self.said.clear()
        self.run_install(skip_build=True)
        self.assertEqual(self.built, ["UE4SS", "proxy", "PythonScripting"])       # not built again
        self.assertIn("UE4SS.dll is up to date", self.said)
        self.assertTrue(any("already links to" in line for line in self.said))

    def test_a_running_game_stops_everything_before_any_change(self):
        with self.assertRaisesRegex(install.InstallError, "is running"):
            self.installer(running=lambda names: names[0]).run(self.win64, "MyGame-Win64-Shipping.exe")
        self.assertEqual(self.built, [])
        self.assertFalse((self.win64 / "UE4SS.dll").exists())

    def test_a_real_mod_folder_is_never_replaced_by_a_link(self):
        touch(self.win64 / "Mods" / "PythonScripting" / "python_scripts" / "main.py", "print('mine')")
        with self.assertRaisesRegex(install.InstallError, "real folder"):
            self.run_install()
        self.assertEqual((self.win64 / "Mods" / "PythonScripting" / "python_scripts" / "main.py").read_text(), "print('mine')")

    def test_copy_mode_copies_the_mod_and_keeps_the_users_scripts(self):
        touch(self.win64 / "Mods" / "PythonScripting" / "python_scripts" / "main.py", "print('mine')")
        self.run_install(copy=True)
        mod = self.win64 / "Mods" / "PythonScripting"
        self.assertFalse(install.is_link(mod))
        self.assertEqual((mod / "dlls" / "main.dll").read_text(), "mod")
        self.assertEqual((mod / "python_scripts" / "main.py").read_text(), "print('mine')")

    def test_a_link_to_another_place_is_re_pointed_without_touching_that_place(self):
        elsewhere = touch(self.root.parent / "old" / "PythonScripting" / "keep.txt", "keep").parent
        (self.win64 / "Mods").mkdir()
        os.symlink(elsewhere, self.win64 / "Mods" / "PythonScripting", target_is_directory=True)
        self.run_install()
        self.assertEqual(os.path.realpath(self.win64 / "Mods" / "PythonScripting"), os.path.realpath(self.root / "dist" / "PythonScripting"))
        self.assertEqual((elsewhere / "keep.txt").read_text(), "keep")

    def test_a_dry_run_changes_nothing(self):
        before = sorted(str(p) for p in Path(self._tmp.name).rglob("*"))
        self.installer(dry_run=True).run(self.win64, "MyGame-Win64-Shipping.exe")
        self.assertEqual(sorted(str(p) for p in Path(self._tmp.name).rglob("*")), before)
        self.assertEqual((self.built, self.packaged), ([], []))
        self.assertIn("dry run: nothing was changed", self.said)

    def test_skip_build_without_built_files_says_what_to_do(self):
        (self.root / "build" / "Game__Shipping__Win64" / "bin" / "UE4SS.dll").unlink()
        with self.assertRaisesRegex(install.InstallError, "build first"):
            self.run_install(skip_build=True)


class LocalConfigTest(unittest.TestCase):
    def test_round_trip_and_a_helpful_error_without_a_game(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "local.config.json"
            self.assertEqual(local_config.load(path), {})
            with self.assertRaisesRegex(local_config.ConfigError, "install.py --game"):
                local_config.game(path)
            local_config.save({"game_dir": "C:/Games/X/Binaries/Win64", "game_exe": "X-Win64-Shipping.exe"}, path)
            self.assertEqual(local_config.game(path), (Path("C:/Games/X/Binaries/Win64"), "X-Win64-Shipping.exe"))
            path.write_text("{ not json")
            with self.assertRaises(local_config.ConfigError):
                local_config.load(path)


if __name__ == "__main__":
    unittest.main()
