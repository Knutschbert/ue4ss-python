# Views of an object's data (struct, array, map, set views, bound functions) must die with the object: after the actor is
# destroyed and garbage collected every use raises InvalidObjectError and nothing crashes, even after its slot is reused.
# Needs the test game (PyTestActor.SpawnPopulated).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


def skip(name, why):
    print(f"{name}: SKIP ({why})")


def raises_invalid(name, fn):
    try:
        fn()
        check(name, False, "no exception")
    except ue4ss.InvalidObjectError:
        check(name, True)
    except Exception as e:
        check(name, False, repr(e))


anchor = ue4ss.find_first_of("PyTestActor")
if anchor is None:
    skip("lifetime of views", "no PyTestActor in this game")
    print("lifetime finished PASS=0 FAIL=0")
else:
    victim = anchor.SpawnPopulated()
    check("a fresh populated actor", victim is not None and victim.is_valid() and victim.get_fname().to_string().startswith("PyTestActor"), victim)

    # views of every kind, taken while the actor is alive
    inner = victim.Inner                 # struct view
    numbers = victim.Inner.Numbers       # array view inside a struct view
    weights = victim.Inner.Weights       # map view inside a struct view
    ints = victim.IntArray               # array view
    names = victim.NameIntMap            # map view
    iset = victim.IntSet                 # set view
    actors = victim.ActorArray           # array of object references
    func = victim.AddInts                # bound function
    owned_inner = victim.Inner.copy()    # an owned copy: must outlive the object
    owned_list = ints.to_list()          # plain Python values
    first_actor = victim.ActorArray[0]   # another object, alive
    weak_target = victim.WeakActor       # reads through to another live object
    views = {"struct view": inner, "array view in a struct": numbers, "map view in a struct": weights, "array view": ints, "map view": names,
             "set view": iset, "object array view": actors}
    check("every view is valid while the owner lives", all(v.is_valid() for v in views.values()) and func.is_valid())
    check("they read correctly", inner.Number == 7 and numbers.to_list() == [10, 20, 30] and len(names) == 3 and sorted(iset) == [2, 3, 5, 7, 11] and func(2, 3) == 5)

    # destroy it and collect garbage
    victim.K2_DestroyActor()
    ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary").CollectGarbage()

    def after_collection():
        check("the destroyed actor reports invalid", not victim.is_valid())
        check("every view reports invalid and does not raise", all(not v.is_valid() for v in views.values()) and not func.is_valid())
        raises_invalid("object: reading a property", lambda: victim.Int32Value)
        raises_invalid("object: writing a property", lambda: setattr(victim, "Int32Value", 1))
        raises_invalid("object: get_full_name", lambda: victim.get_full_name())
        raises_invalid("bound function: calling", lambda: func(1, 2))
        raises_invalid("struct view: reading", lambda: inner.Number)
        raises_invalid("struct view: writing", lambda: setattr(inner, "Number", 1))
        raises_invalid("struct view: to_dict", lambda: inner.to_dict())
        raises_invalid("array view in a struct: len", lambda: len(numbers))
        raises_invalid("array view: indexing", lambda: ints[0])
        raises_invalid("array view: writing", lambda: ints.__setitem__(0, 1))
        raises_invalid("array view: append", lambda: ints.append(1))
        raises_invalid("array view: iteration", lambda: list(ints))
        raises_invalid("array view: to_list", lambda: ints.to_list())
        raises_invalid("object array view: indexing", lambda: actors[0])
        raises_invalid("map view in a struct: len", lambda: len(weights))
        raises_invalid("map view: lookup", lambda: names["one"])
        raises_invalid("map view: writing", lambda: names.__setitem__("one", 5))
        raises_invalid("set view: contains", lambda: 2 in iset)
        raises_invalid("set view: add", lambda: iset.add(99))
        check("repr never raises", all(isinstance(repr(x), str) for x in list(views.values()) + [victim, func]))
        check("an owned copy outlives the object", owned_inner.Number == 7 and owned_inner.Label == "inner label" and owned_inner.is_valid())
        check("plain values outlive the object", owned_list == [3, 1, 4, 1, 5, 9, 2, 6])
        check("other objects are unaffected", first_actor.is_valid() and weak_target.is_valid() and anchor.AddInts(1, 1) == 2)

        # make many new actors so the dead actor's slot (and maybe its address) is reused: old views must stay dead
        made = anchor.SpawnMore(60)
        fresh = ue4ss.find_all_of("Actor")
        check("new objects were created", made == 60 and len(fresh) >= 60, made)
        check("the dead views did not come back to life", not victim.is_valid() and all(not v.is_valid() for v in views.values()))
        check("the dead object does not equal any new object", all(victim != o for o in fresh))
        raises_invalid("after slot churn: struct view", lambda: inner.Number)
        raises_invalid("after slot churn: array view", lambda: ints[0])
        check("alive objects hash and compare normally", len({o for o in fresh}) == len(fresh))
        print(f"lifetime finished PASS={sum(results)} FAIL={len(results) - sum(results)}")

    ue4ss.execute_with_delay(1500, after_collection)
