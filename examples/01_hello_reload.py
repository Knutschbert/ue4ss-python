# 01 - Hello, and reload with a key.
#
# Copy this file to <Game>\Binaries\Win64\Mods\PythonScripting\python_scripts\main.py and start the game.
# print() goes to UE4SS.log (next to the game's executable) as "[Python] ...".
#
# main.py runs once, on the game thread, at the first engine tick. What it registers (keys, hooks, delayed
# tasks) keeps working after the script has finished.
import ue4ss
from ue4ss import Key

# F5 runs main.py again: edit the file, save, press F5 in the game. A reload first removes every key
# binding, hook and pending task of the previous run, so nothing is registered twice.
ue4ss.register_key_down(Key.F5, ue4ss.reload)

print("hello from Python; this is run number", ue4ss.script_generation())

# A delayed task: runs on the game thread, at least two seconds from now.
ue4ss.execute_with_delay(2000, lambda: print("two seconds later"))
