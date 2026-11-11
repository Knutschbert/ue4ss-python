# Copy over python_scripts/main.py to check owned struct values in the game, including a leak test.
# Every write to the game is restored.
import ctypes
import ctypes.wintypes as wt
import gc
import threading

import ue4ss


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


def raises(label, exc, fn):
    try:
        fn()
    except exc as e:
        print(f"{label}: PASS ({type(e).__name__}: {e})")
        return
    except BaseException as e:
        print(f"{label}: FAIL (got {type(e).__name__}: {e})")
        return
    print(f"{label}: FAIL (no exception)")


# --- make_struct -----------------------------------------------------------------------------
vec = ue4ss.make_struct("/Script/CoreUObject.Vector", X=1.0, Y=2.0, Z=3.0)
print("vector:", vec, vec.to_dict(), "| owned:", vec.is_owned())
check("make_struct from a path", vec.is_owned() and vec.to_dict() == {"X": 1.0, "Y": 2.0, "Z": 3.0})
vec_type = ue4ss.static_find_object("/Script/CoreUObject.Vector")
check("make_struct from a UScriptStruct", ue4ss.make_struct(vec_type).to_dict() == {"X": 0.0, "Y": 0.0, "Z": 0.0})
check("default-initialised", ue4ss.make_struct("/Script/CoreUObject.Rotator").to_dict() == {"Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0})

url = ue4ss.make_struct("/Script/Engine.URL", Host="example.org", Port=80, Map="Arena")
print("url:", url, "| fields:", url.get_property_names())
check("struct with FStrings", (url.Host, url.Port, url.Map) == ("example.org", 80, "Arena"))
print("defaults from the engine's constructor: Protocol =", repr(url.Protocol), "| Portal =", repr(url.Portal), "| Port before override was 7777 by default")
check("untouched FStrings hold the C++ constructor's defaults", isinstance(url.Protocol, str) and url.Portal == "")
url.Host = "h\u00e9llo \U0001F600"
check("write non-ASCII FString into an owned struct", url.Host == "h\u00e9llo \U0001F600")
check("array field of an owned struct is an Array", isinstance(url.Op, ue4ss.Array) and len(url.Op) == 0)

# --- copies ----------------------------------------------------------------------------------
url2 = url.copy()
url2.Host = "changed"
check("deep copy: strings are independent", url.Host == "h\u00e9llo \U0001F600" and url2.Host == "changed")
check("copy is owned and equal in contents", url2.is_owned() and url.copy().to_dict() == url.to_dict())

gm = ue4ss.find_first_of("GameModeBase")
original = gm.ReplicatedMovement.to_dict()
try:
    gm.ReplicatedMovement.LinearVelocity.X = 5.0
    snapshot = gm.ReplicatedMovement.LinearVelocity.copy()
    check("copy of a view is owned", snapshot.is_owned() and snapshot.X == 5.0)
    gm.ReplicatedMovement.LinearVelocity.X = 6.0
    check("view write does not reach the copy", snapshot.X == 5.0)
    snapshot.Y = 7.0
    check("copy write does not reach the object", gm.ReplicatedMovement.LinearVelocity.Y == original["LinearVelocity"]["Y"])

    gm.ReplicatedMovement.AngularVelocity = snapshot
    check("assign an owned struct to a struct property", gm.ReplicatedMovement.AngularVelocity.to_dict() == {"X": 5.0, "Y": 7.0, "Z": original["LinearVelocity"]["Z"]})
    snapshot.X = 100.0
    check("later changes do not reach the property", gm.ReplicatedMovement.AngularVelocity.X == 5.0)
finally:
    gm.ReplicatedMovement.LinearVelocity = original["LinearVelocity"]
    gm.ReplicatedMovement.AngularVelocity = original["AngularVelocity"]
check("game object restored", gm.ReplicatedMovement.to_dict() == original)

raises("assign a struct of the wrong type", ue4ss.UEError, lambda: setattr(gm.ReplicatedMovement, "LinearVelocity", ue4ss.make_struct("/Script/CoreUObject.Rotator")))
raises("make_struct of a class", ue4ss.UEError, lambda: ue4ss.make_struct("/Script/Engine.Actor"))
raises("make_struct of a missing path", ue4ss.UEError, lambda: ue4ss.make_struct("/Script/Nope.Nothing"))
raises("make_struct of a plain object", ue4ss.UEError, lambda: ue4ss.make_struct(gm))
raises("make_struct wrong argument type", TypeError, lambda: ue4ss.make_struct(5))
raises("make_struct bad field", AttributeError, lambda: ue4ss.make_struct("/Script/CoreUObject.Vector", Nope=1))
raises("make_struct bad field value", TypeError, lambda: ue4ss.make_struct("/Script/CoreUObject.Vector", X="1"))
raises("make_struct field overflow", OverflowError, lambda: ue4ss.make_struct("/Script/Engine.URL", Port=2**31))

# --- are owned structs released? ---------------------------------------------------------------------
# Deterministic: the registry counts creations and destructions. (Process memory is printed for information only;
# the game is still loading and its private memory drifts by hundreds of MB between readings.)
class Counters(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
_kernel32.K32GetProcessMemoryInfo.restype = wt.BOOL


def private_mb():
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    _kernel32.K32GetProcessMemoryInfo(_kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.PrivateUsage / (1024 * 1024)


def stats():
    return ue4ss._selftest_struct_stats()


BIG = "x" * (256 * 1024)  # 512 KB as UTF-16
COUNT = 200
gc.collect()
base = stats()
print("stats at start:", base)
check("stats keys", set(base) == {"created", "destroyed", "queued", "leaked_inner", "leaked_block", "live"})

mem_before = private_mb()
held = [ue4ss.make_struct("/Script/Engine.URL", Host=BIG) for _ in range(COUNT)]
mem_peak = private_mb()
now = stats()
check("each make_struct is counted", now["created"] - base["created"] == COUNT)
print(f"(info) private memory {mem_before:.0f} MB -> {mem_peak:.0f} MB with {COUNT} x 512 KB strings held")

copies = [s.copy() for s in held[:50]]
check("copies are counted too", stats()["created"] - base["created"] == COUNT + 50)
del copies
held.clear()
gc.collect()
now = stats()
check("dropping on the game thread destroys immediately", now["destroyed"] - base["destroyed"] == COUNT + 50 and now["live"] == base["live"])
check("none queued, none leaked on the game thread", now["queued"] == base["queued"] and now["leaked_inner"] == base["leaked_inner"] and now["leaked_block"] == base["leaked_block"])
print(f"(info) private memory after dropping: {private_mb():.0f} MB")

held = [ue4ss.make_struct("/Script/Engine.URL", Host=BIG) for _ in range(COUNT)]
mid = stats()
def drop_on_python_thread():
    held.clear()  # the last references die here, off the game thread
    gc.collect()
worker = threading.Thread(target=drop_on_python_thread)
worker.start()
worker.join()
right_after = stats()
print("right after the Python thread dropped them:", right_after)
check("off-thread drops are queued, not destroyed on the spot", right_after["queued"] - mid["queued"] == COUNT and right_after["live"] > base["live"])


def later():
    after = stats()
    print("one second later:", after)
    check("queued destructions ran on the game thread", after["live"] == base["live"] and after["destroyed"] - mid["destroyed"] == COUNT)
    check("nothing leaked", after["leaked_inner"] == base["leaked_inner"] and after["leaked_block"] == base["leaked_block"])

    # Churn: many short-lived owned structs and copies.
    start_stats = stats()
    start = private_mb()
    for i in range(20000):
        s = ue4ss.make_struct("/Script/Engine.URL", Host="host-%d" % i, Port=i % 1000)
        s2 = s.copy()
        s2.Map = "map-%d" % i
    del s, s2  # the last iteration's structs are still referenced by these names
    gc.collect()
    end_stats = stats()
    check("churn: every struct destroyed", end_stats["live"] == start_stats["live"] and end_stats["created"] - start_stats["created"] == 40000)
    grown = private_mb() - start
    print(f"(info) 20000 make_struct+copy pairs: private memory grew {grown:.1f} MB")
    print("owned structs test finished")


ue4ss.execute_with_delay(1500, later)
