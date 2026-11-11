# Changelog

## Unreleased

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
- Build, package and release tooling; the UE 4.25 test project.
