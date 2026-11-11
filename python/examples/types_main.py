# Copy over python_scripts/main.py to check FName and FString conversions in the game. Prints PASS/FAIL per check.
import ue4ss


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


name = ue4ss.FName("PyueTestName_7")
check("FName text", str(name) == "PyueTestName_7", repr(name))
check("equal to an FName of the same text", name == ue4ss.FName("PyueTestName_7"))
check("equal to a str of another case", name == "pyuetestname_7")
check("not equal to another name", name != "PyueTestName_8" and name != ue4ss.FName("PyueTestName_8"))
check("number part shares the base index", ue4ss.FName("PyueTestName").comparison_index == name.comparison_index
      and name.number != ue4ss.FName("PyueTestName").number)
check("FName.find of a name never created is None", ue4ss.FName.find("PyueNeverCreatedName_xyz") is None)
check("FName.find is case-insensitive and gives the canonical spelling", str(ue4ss.FName.find("pyuetestname_7")) == "PyueTestName_7")
check("hash is stable", hash(name) == hash(ue4ss.FName("PyueTestName_7")))
none_name = ue4ss.FName("None")
check("the None name has index 0", str(none_name) == "None" and none_name.comparison_index == 0)
check("an engine name has a non-zero index", ue4ss.FName("Object").comparison_index != 0)

text = "héllo \U0001F600 ✓"
back = ue4ss._selftest_fstring(text)
check("FString round trip keeps non-ASCII text", back == text and len(back) == len(text), ascii(back))
wide = ue4ss.FName("Pyue" + text)
check("non-ASCII FName round trip", str(wide) == "Pyue" + text, ascii(str(wide)))

for bad in (5, None):
    try:
        ue4ss.FName(bad)
        check(f"FName({bad!r}) raises TypeError", False, "no exception")
    except TypeError as e:
        check(f"FName({bad!r}) raises TypeError", True, str(e))
print("types example finished")
