# A fault inside game code during a call must become a UEError and leave the game running. The fault is provoked by handing
# KismetSystemLibrary.GetObjectName a pointer to unmapped memory (planted with _selftest_poison_pointer).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:260] if detail else ""))


library = ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary")
function = ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary:GetObjectName")
actor_class = ue4ss.static_find_object("/Script/Engine.Actor")

# a good call first
check("GetObjectName works normally", library.GetObjectName(actor_class) == "Actor", library.GetObjectName(actor_class))

before = ue4ss._selftest_faults_survived()
block = function.make_param_block(actor_class)
ue4ss._selftest_poison_pointer(block, "Object", 1)  # unmapped address
try:
    ue4ss._selftest_call_with_block(library, function, block)
    check("a faulting call raises", False, "no exception")
except ue4ss.UEError as e:
    check("a faulting call raises UEError", "faulted" in str(e), e)
    check("the message names the access", "access violation" in str(e) and "reading" in str(e), e)
except Exception as e:
    check("a faulting call raises UEError", False, repr(e))
check("the fault is counted", ue4ss._selftest_faults_survived() == before + 1, ue4ss._selftest_faults_survived())

# the process survived: normal calls, hooks and the game thread machinery still work
check("normal call after the fault", library.GetObjectName(actor_class) == "Actor")
math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
check("another library after the fault", math.Add_IntInt(2, 3) == 5)
hits = []
ids = ue4ss.register_hook("/Script/Engine.KismetMathLibrary:Add_IntInt", lambda c, p: hits.append(1))
math.Add_IntInt(1, 1)
ue4ss.unregister_hook(ids)
check("hooks still fire after the fault", len(hits) == 1, len(hits))

# repeat: ten faults in a row
count = 0
for i in range(10):
    block = function.make_param_block(actor_class)
    ue4ss._selftest_poison_pointer(block, "Object", 1)
    try:
        ue4ss._selftest_call_with_block(library, function, block)
    except ue4ss.UEError:
        count += 1
check("ten faults in a row all became UEError", count == 10, count)
check("still alive after them", math.Add_IntInt(40, 2) == 42)


def later():
    check("game thread tasks still run after the faults", True)
    print(f"fault finished PASS={sum(results)} FAIL={len(results) - sum(results)}")


ue4ss.execute_with_delay(1500, later)
