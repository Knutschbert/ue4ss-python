# Changelog

## Unreleased

- MCP server for the live bridge: `.mcp.json`, clear answers without a running game, `live_wait_ready`; `AGENTS.md` for agents.
- `docs/ENGINE_VERSIONS.md`: which engine versions are supported and how to add one.
- `tools/live.py`: run Python in the running game from a terminal (file-based bridge, with an MCP adapter).
- `tools/make_release.py --build --with-ue4ss`: one command to a release zip that includes the matching UE4SS.
- `tools/testgame.py`: install into the test game and run the in-game test scripts automatically.
- `tools/install.py`: build, package and install into a game in one command; `tools/start_game.py`, `tools/follow_log.py`.
- Packaging writes a starter `main.py` when there is none and keeps the scripts already in the installed folder.
- Example scripts for users in `examples/`.
- Typings of the base `ue4ss` module are shipped in `python/typings`.
- `tools/setup_windows.ps1` checks out the tested RE-UE4SS commit by default (`-Ue4ssRef` selects another).

## 0.1

First version in this repository.

- The `ue4ss` module: finding objects, properties of all reflected kinds, arrays, maps, sets, structs, calling functions,
  hooks, replacements, binding to event dispatchers (also with the callback on a worker thread), new-object notifications, key bindings, game-thread scheduling,
  reloading.
- `ue4ss.engine`: engine classes as Python types and generated `.pyi` typings.
- The off-game `ue4ss` module for tests.

Known defects: `bind_event` does not deliver broadcasts of delegates declared in C++ (Blueprint event dispatchers work).
- Build, package and release tooling; the UE 4.25 test project.
