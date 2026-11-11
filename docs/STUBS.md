# Stub generation: input format decision

Decision for `.pyi` stub generation (ticket wzm.1).

## Options surveyed (RE-UE4SS 97adba0)
| Source | Content | Fit |
|---|---|---|
| `.jmap` (`UE4SS/src/JMapGenerator`) | JSON reflection dump: classes, functions, script structs, enums, property offsets/sizes/flags, CDO values. Written to `<GameName>-<EngineVersion>-<sha>.jmap` in the UE4SS working dir; Ctrl+Numpad5, GUI Dumpers tab, or Lua `DumpJMAP()`. | **Chosen.** Machine readable, complete, and includes functions with params. |
| `.usmap` (`USMapGenerator`) | Binary property mappings for unversioned properties | No functions; binary format. |
| SDK generator (`SDKGenerator`, UHT-style headers, `JSONDumper`) | C++ headers / JSON of Blueprint classes only | Headers are the wrong shape; JSON covers Blueprint-generated classes only. |
| Object dump (`ObjectDumper`) | Text list of objects | No type detail. |
| In-process reflection from our own mod | Same data as jmap, via the Unreal wrapper | Possible later (ticket wzm.3), needs UEPseudo access and the mod running. |

## Decision
`tools/gen_stubs.py` (stdlib only, runs in WSL or Windows) reads a `.jmap` file and writes `.pyi` stubs. The in-process `ue4ss.dump_stubs()` (wzm.3) is optional and only worth doing if jmap proves insufficient for 4.25 games.

## Open points
- Whether the jmap dumper works on a UE 4.25 target is unverified; the generator code handles `Version::IsBelow(4, 25)`, so the format is version aware. Checked by the `[WIN]` stub ticket (wzm.4), which needs a real `.jmap` from the game.
- The wzm.2 fixture is written from the public jmap schema (github.com/trumank/jmap), not from a real game dump.
