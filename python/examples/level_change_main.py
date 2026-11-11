# Wrappers and views held across a level change and a forced garbage collection must raise InvalidObjectError (no crash), and
# the delete listener must keep working: counts and threads of the deletions are recorded (docs/LIFETIME.md, ticket 2kb.6).
# Needs the test game: PyTestActor objects are recreated by the game mode when the map is loaded again.
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


def raises_invalid(name, fn):
    try:
        fn()
        check(name, False, "no exception")
    except ue4ss.InvalidObjectError:
        check(name, True)
    except Exception as e:
        check(name, False, repr(e))


old_actors = sorted(ue4ss.find_all_of("PyTestActor"), key=lambda a: a.get_fname().to_string())
if len(old_actors) != 3:
    print(f"level change: SKIP (needs the three PyTestActor objects of the test game, found {len(old_actors)})")
    print("level change finished PASS=0 FAIL=0")
else:
    old_world = ue4ss.find_first_of("World")
    first = old_actors[0]
    views = {"struct view": first.Inner, "array view": first.IntArray, "map view": first.NameIntMap, "set view": first.IntSet,
             "actor array view": first.ActorArray}
    bound = first.AddInts
    owned = first.Inner.copy()
    before = ue4ss._selftest_objects()
    print("delete listener before:", {k: v for k, v in before.items() if k.startswith("deleted")})
    check("everything is valid before the level change", old_world.is_valid() and all(a.is_valid() for a in old_actors) and all(v.is_valid() for v in views.values()))

    state = {"phase": "travel", "polls": 0}
    library = ue4ss.static_find_object("/Script/Engine.GameplayStatics")

    def travel():
        # absolute travel to the same map: the current world is torn down and a new one is built
        library.OpenLevel(old_world, "/Engine/Maps/Entry", True, "")
        print("OpenLevel called")
        ue4ss.execute_with_delay(500, poll)

    def poll():
        state["polls"] += 1
        fresh = [a for a in ue4ss.find_all_of("PyTestActor") if a.is_valid() and a not in old_actors]
        if len(fresh) < 3 and state["polls"] < 40:
            ue4ss.execute_with_delay(500, poll)
            return
        check("the new level has its own populated test actors", len(fresh) == 3, len(fresh))
        ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary").CollectGarbage()
        ue4ss.execute_with_delay(1500, verify)

    def verify():
        check("old actors are invalid after the level change and GC", all(not a.is_valid() for a in old_actors), [a.is_valid() for a in old_actors])
        check("the old world is invalid", not old_world.is_valid())
        check("every held view is invalid and says so without raising", all(not v.is_valid() for v in views.values()) and not bound.is_valid())
        raises_invalid("old actor: property read", lambda: first.Int32Value)
        raises_invalid("old actor: property write", lambda: setattr(first, "Int32Value", 1))
        raises_invalid("old actor: function call", lambda: first.AddInts(1, 2))
        raises_invalid("bound function call", lambda: bound(1, 2))
        for name, view in views.items():
            raises_invalid(f"{name}: use", (lambda v: (lambda: (len(v) if hasattr(v, '__len__') else v.Number)))(view))
        check("repr never raises", all(isinstance(repr(x), str) for x in list(views.values()) + old_actors + [old_world, bound]))
        check("an owned copy taken before survives", owned.Number == 7 and owned.is_valid())
        new_actors = sorted(ue4ss.find_all_of("PyTestActor"), key=lambda a: a.get_fname().to_string())
        check("the new test actors work normally", len(new_actors) == 3 and new_actors[0].AddInts(2, 3) == 5 and new_actors[0].IntArray.to_list()[:2] == [3, 1])
        check("old and new objects never compare equal", all(o != n for o in old_actors for n in new_actors))
        after = ue4ss._selftest_objects()
        delta = {k: after[k] - before[k] for k in before if k.startswith("deleted")}
        print("delete listener across the level change:", delta)
        check("the delete listener saw the mass deletion", delta["deleted_total"] >= 50, delta)
        check("every deletion was attributed to some thread", delta["deleted_on_game_thread"] + delta["deleted_off_game_thread"] + delta["deleted_before_game_thread_known"] == delta["deleted_total"], delta)
        print(f"DELETIONS total={delta['deleted_total']} game_thread={delta['deleted_on_game_thread']} off_game_thread={delta['deleted_off_game_thread']} before_known={delta['deleted_before_game_thread_known']}")
        print(f"level change finished PASS={sum(results)} FAIL={len(results) - sum(results)}")

    ue4ss.execute_with_delay(500, travel)
