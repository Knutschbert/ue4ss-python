# Replacements run INSTEAD of a function (ProcessEvent-routed calls). Uses the test game's PyTestActor: Python calls reach
# ProcessEvent, and Fire() makes C++ call bound functions through ProcessEvent with no Python involved.
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


def raises(name, exc, fn):
    try:
        fn()
        check(name, False, "no exception")
    except exc:
        check(name, True)
    except Exception as e:
        check(name, False, repr(e))


actors = sorted(ue4ss.find_all_of("PyTestActor"), key=lambda a: a.get_fname().to_string())
if len(actors) != 3:
    print(f"replacements: SKIP (needs the three PyTestActor objects of the test game, found {len(actors)})")
    print("replacement checks finished PASS=0 FAIL=0")
else:
    a0, a1, a2 = actors
    ADD = "/Script/PyTestGame.PyTestActor:AddInts"
    HANDLED = "/Script/PyTestGame.PyTestActor:OnHandled"
    SPLIT = "/Script/PyTestGame.PyTestActor:SplitVector"

    # 1. replacement result, original skipped
    seen = []
    rid = ue4ss.register_replacement(ADD, lambda context, params: seen.append((context.get_fname().to_string(), params.A, params.B)) or 99)
    check("replacement id is an int", isinstance(rid, int))
    check("the replacement's result is returned", a0.AddInts(2, 3) == 99)
    check("it saw the context and parameters", seen == [("PyTestActor_0", 2, 3)], seen)
    check("it applies to every instance", a2.AddInts(1, 1) == 99)

    # 2. conditional: CALL_ORIGINAL
    ue4ss.unregister_replacement(rid)
    check("after unregistering the function is normal again", a0.AddInts(2, 3) == 5)
    rid = ue4ss.register_replacement(ADD, lambda context, params: ue4ss.CALL_ORIGINAL if params.A >= 0 else -1)
    check("CALL_ORIGINAL runs the function", a0.AddInts(2, 3) == 5)
    check("another case is replaced", a0.AddInts(-2, 3) == -1)
    ue4ss.unregister_replacement(rid)

    # 3. wrapping the original from inside the replacement
    rid = ue4ss.register_replacement(ADD, lambda context, params: context.AddInts(params.A, params.B) * 10)
    check("a replacement can wrap the original (nested call runs the original)", a0.AddInts(2, 3) == 50)
    ue4ss.unregister_replacement(rid)

    # 4. the original really is skipped: OnHandled has a visible side effect
    before = (a0.LastHandled, a0.HandledCount)
    calls = []
    rid = ue4ss.register_replacement(HANDLED, lambda context, params: calls.append((context.get_fname().to_string(), params.Value)))
    a0.OnHandled(321)
    check("the original's side effect did not happen", (a0.LastHandled, a0.HandledCount) == before, (a0.LastHandled, a0.HandledCount))
    check("the replacement ran for the Python call", calls == [("PyTestActor_0", 321)], calls)

    # 5. calls made by C++ (Fire broadcasts the bound delegates): no Python in the call path
    calls.clear()
    counts_before = [(a.LastHandled, a.HandledCount) for a in actors]
    a0.Fire(55)
    check("C++ delegate calls reach the replacement", len(calls) >= 1 and all(v == 55 for _, v in calls), calls)
    check("and their originals did not run", [(a.LastHandled, a.HandledCount) for a in actors] == counts_before)
    ue4ss.unregister_replacement(rid)
    a0.Fire(66)
    check("after unregistering, C++ delegate calls run the original again", a0.LastHandled == 66 and a0.HandledCount > before[1], (a0.LastHandled, a0.HandledCount))

    # 6. output parameters set through params
    def split(context, params):
        params.X, params.Y, params.Z = 7.0, 8.0, 9.0
        return None
    rid = ue4ss.register_replacement(SPLIT, split)
    check("output parameters can be set on params", a0.SplitVector({"X": 1.0, "Y": 2.0, "Z": 3.0}) == (7.0, 8.0, 9.0))
    ue4ss.unregister_replacement(rid)
    check("outputs normal again", a0.SplitVector({"X": 1.0, "Y": 2.0, "Z": 3.0}) == (1.0, 2.0, 3.0))

    # 7. mistakes run the original and are reported
    rid = ue4ss.register_replacement(ADD, lambda context, params: "not an int")
    check("a result of the wrong type is reported and the original runs", a0.AddInts(2, 3) == 5)
    ue4ss.unregister_replacement(rid)
    rid = ue4ss.register_replacement(ADD, lambda context, params: 1 / 0)
    check("an exception is reported and the original runs", a0.AddInts(4, 5) == 9)
    ue4ss.unregister_replacement(rid)

    # 8. registration errors
    rid = ue4ss.register_replacement(ADD, lambda c, p: 0)
    raises("a second replacement for the same function is refused", ue4ss.UEError, lambda: ue4ss.register_replacement(ADD, lambda c, p: 1))
    ue4ss.unregister_replacement(rid)
    raises("unregistering twice raises ValueError", ValueError, lambda: ue4ss.unregister_replacement(rid))
    raises("an unknown function is refused", ue4ss.UEError, lambda: ue4ss.register_replacement("/Script/PyTestGame.PyTestActor:NoSuchFunction", print))
    raises("a non-callable is refused", TypeError, lambda: ue4ss.register_replacement(ADD, 5))
    raises("a non-str path is refused", TypeError, lambda: ue4ss.register_replacement(5, print))

    # 9. a hook on the same function does not fire for a replaced call
    hook_calls = []
    hook_ids = ue4ss.register_hook(ADD, lambda c, p: hook_calls.append("pre"), lambda c, p, r: hook_calls.append("post"))
    rid = ue4ss.register_replacement(ADD, lambda c, p: 5)
    a0.AddInts(1, 1)
    check("hooks do not run for a replaced call", hook_calls == [], hook_calls)
    ue4ss.unregister_replacement(rid)
    a0.AddInts(1, 1)
    check("but run again once the replacement is gone", hook_calls == ["pre", "post"], hook_calls)
    ue4ss.unregister_hook(hook_ids)

    # 10. the engine keeps running with many calls
    rid = ue4ss.register_replacement(ADD, lambda c, p: p.A + p.B + 1000)
    check("1000 replaced calls", all(a0.AddInts(i, 1) == i + 1001 for i in range(1000)))
    ue4ss.unregister_replacement(rid)
    check("normal after all of it", a0.AddInts(20, 22) == 42)
    print(f"replacement checks finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
