# A bad object pointer left in game memory must read as None and never crash. The pointer is planted by hand in the
# parameter block of KismetSystemLibrary.IsValid (a plain UObject* field).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:200] if detail else ""))


function = ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary:IsValid")
block = function.make_param_block(ue4ss.static_find_object("/Script/Engine.Actor"))
good = ue4ss.static_find_object("/Script/Engine.Actor")
block.Object = good
check("a good pointer reads back", block.Object == good)

modes = {0: "freed heap memory", 1: "unmapped address", 2: "inside a live object", 3: "misaligned", 4: "small integer"}
for mode, label in modes.items():
    block.Object = good
    ue4ss._selftest_poison_pointer(block, "Object", mode)
    try:
        value = block.Object
        check(f"{label} reads as None", value is None, value)
    except Exception as e:
        check(f"{label} reads as None", False, repr(e))

# many reads of every bad kind, the process must survive
bad = 0
for i in range(2000):
    ue4ss._selftest_poison_pointer(block, "Object", i % 5)
    if block.Object is not None:
        bad += 1
check("2000 reads of poisoned pointers", bad == 0, bad)

# calling with a poisoned argument block must not be needed; but the block is still usable afterwards
block.Object = good
check("block usable after poisoning", block.Object == good)
try:
    ue4ss._selftest_poison_pointer(block, "ReturnValue", 0)
    check("non-pointer field refused", False)
except ue4ss.UEError:
    check("non-pointer field refused", True)
print(f"poison finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
