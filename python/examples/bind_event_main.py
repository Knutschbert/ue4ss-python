# bind_event / unbind_event: binding a fresh Python callback to a multicast delegate (Event Dispatcher) property on an
# object. Uses the test game's PyTestActor, which has an OnMulti multicast delegate and a Fire(Value) method that broadcasts
# it from C++: a broadcast runs the delegate's __DelegateSignature function through ProcessEvent on the bound object, which
# the mod's hook routes to the Python callback with (context, params).
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
if not actors:
    print("bind_event: SKIP (needs the PyTestActor objects of the test game)")
    print("bind_event checks finished PASS=0 FAIL=0")
else:
    a0 = actors[0]

    # 1. a broadcast of OnMulti delivers the callback with context and the delegate's parameter
    seen = []
    eid = ue4ss.bind_event(a0, "OnMulti", lambda context, params: seen.append((context, params.Value)))
    a0.Fire(55)  # C++ broadcasts OnMulti with Value=55
    check("the bound callback ran on the broadcast", len(seen) == 1, seen)
    check("context is the object whose delegate fired", len(seen) == 1 and seen[0][0] == a0, [s[0].get_fname().to_string() for s in seen])
    check("params holds the delegate's argument", len(seen) == 1 and seen[0][1] == 55, seen)

    # 2. several bindings on the same delegate each run, once per broadcast
    calls = []
    ue4ss.bind_event(a0, "OnMulti", lambda c, p: calls.append("first"))
    ue4ss.bind_event(a0, "OnMulti", lambda c, p: calls.append("second"))
    a0.Fire(66)
    check("each binding runs once per broadcast", calls == ["first", "second"], calls)

    # 3. a broadcast on another PyTestActor (independent delegate value) does not reach this binding
    call_count = []
    only = ue4ss.bind_event(a0, "OnMulti", lambda c, p: call_count.append(c))
    a1 = actors[1]
    a1.Fire(77)
    check("a different object's broadcast does not reach the binding", call_count == [], [c.get_fname().to_string() for c in call_count])
    a0.Fire(88)
    check("the same object's broadcast still does", len(call_count) == 1 and call_count[0] == a0, [c.get_fname().to_string() for c in call_count])

    # 4. unbind stops delivery for that binding only
    a0.Fire(1)
    n_before = len(call_count)
    ue4ss.unbind_event(only)
    a0.Fire(2)
    check("unbind stops delivery", len(call_count) == n_before, call_count)

    # 5. error cases
    raises("binding to a missing property is refused", AttributeError, lambda: ue4ss.bind_event(a0, "NoSuchProperty", lambda c, p: None))
    raises("binding to a non-multicast property is refused", ue4ss.UEError, lambda: ue4ss.bind_event(a0, "Int32Value", lambda c, p: None))
    raises("a non-callable callback is refused", TypeError, lambda: ue4ss.bind_event(a0, "OnMulti", 5))
    raises("unbinding an unknown id is refused", ValueError, lambda: ue4ss.unbind_event(999999))
    raises("binding on a non-object is refused", TypeError, lambda: ue4ss.bind_event("nope", "OnMulti", lambda c, p: None))

    # 6. a fresh binding/unbinding round-trips cleanly
    another = ue4ss.bind_event(a0, "OnMulti", lambda c, p: None)
    ue4ss.unbind_event(another)
    check("bind and unbind round-trip", True)

    print(f"bind_event checks finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
