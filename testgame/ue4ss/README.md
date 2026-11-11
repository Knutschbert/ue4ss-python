# UE4SS files for the test game

Copy these next to the staged test game's executable (see `docs/TESTGAME.md`).

- `UE4SS-settings.ini`: UE4SS settings for a UE 4.25 game with `ConsoleEnabled`, `GuiConsoleEnabled` and
  `GuiConsoleVisible` set to 0 (no UE4SS window during tests). Turn them on for debugging.
- `Mods/mods.txt`: only `PythonScripting` is enabled. The Python mod itself comes from `dist/PythonScripting`.

`UE4SS.dll` and the proxy `dwmapi.dll` are not stored here: build both from the same RE-UE4SS checkout the mod is built
against (`docs/BUILDING.md`) and copy them next to the executable.
