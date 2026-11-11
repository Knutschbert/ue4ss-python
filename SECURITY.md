# Security

## What a script can do

A script run by this mod is ordinary Python inside the game process. It has the full standard library and the rights of
the user who started the game: it can read and write files, open network connections and start programs, and it can read
and change anything in the game's memory.

**Run only scripts you trust**, exactly as you would treat any other mod or program. The mod does not sandbox scripts and
is not meant to.

The mod itself opens no network connection and reads no file other than its own folder (`dlls`, `python_scripts`).

## Online games

Changing a game's memory can violate the terms of a game or trip its anti-cheat. Use the mod where modding is allowed:
single-player, your own servers, or games that permit it.

## Reporting a problem

If you find a way in which the mod itself (not a script) exposes users, for example by loading code from an unexpected
place, please report it privately to the maintainer before publishing details.
