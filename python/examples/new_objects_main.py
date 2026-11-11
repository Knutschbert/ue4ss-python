# Checks notify_on_new_object in the game. The game creates objects all the time (UI, effects, ...), so we subscribe to
# everything for a few seconds and check what arrives, then to a narrow class, then unsubscribe and check it stops.
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail) if detail else ""))


# argument errors
for label, call, exc in (
    ("non-str path", lambda: ue4ss.notify_on_new_object(5, print), TypeError),
    ("non-callable", lambda: ue4ss.notify_on_new_object("/Script/Engine.Actor", 5), TypeError),
    ("unknown class", lambda: ue4ss.notify_on_new_object("/Script/Engine.NoSuchClassAtAll", print), ue4ss.UEError),
    ("not a class", lambda: ue4ss.notify_on_new_object("/Script/Engine", print), ue4ss.UEError),
    ("unknown id", lambda: ue4ss.unregister_notify_on_new_object(987654), ValueError),
):
    try:
        call()
        check(label, False, "no exception")
    except exc:
        check(label, True)
    except Exception as e:
        check(label, False, repr(e))

names_before = ue4ss.FName.find("PyueNeverUsedNameForNotify")
check("test name not in the table yet", names_before is None)

seen = []
bad = []
off_thread = []


def on_any(obj):
    if not ue4ss.is_game_thread():
        off_thread.append(1)
    seen.append(obj)
    try:
        if not obj.is_valid() or not obj.is_a("/Script/CoreUObject.Object"):
            bad.append(("invalid", 0))
        obj.get_full_name()
    except Exception as e:
        bad.append((repr(e)[:80], 0))


any_id = ue4ss.notify_on_new_object("/Script/CoreUObject.Object", on_any)
check("subscription id is an int", isinstance(any_id, int))

actor_seen = []
actor_id = ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda obj: actor_seen.append(obj.get_class().get_full_name()))

raised = []

# Make some objects now instead of waiting for the game to: the test game's actor can spawn actors on demand (and spawn-then-
# destroy some, which must never be delivered). Games without it (Chivalry 2) just create objects by themselves.
spawner = ue4ss.find_first_of("PyTestActor")
spawned_now = destroyed_now = 0
if spawner is not None:
    spawned_now = spawner.SpawnMore(4)
    destroyed_now = spawner.SpawnAndDestroy(3)
    check("the test actor spawned what was asked", (spawned_now, destroyed_now) == (4, 3), (spawned_now, destroyed_now))


def raising(obj):
    raised.append(1)
    raise RuntimeError("deliberate test error in a new-object callback")


raise_id = ue4ss.notify_on_new_object("/Script/Engine.Actor", raising)


def phase_two():
    n_any = len(seen)
    check("objects were delivered", n_any > 0, n_any)
    if spawner is not None:
        fresh = [o for o in seen if o.is_valid() and o.get_class().get_fname().to_string() == "Actor"]
        check("every spawned actor that stayed was delivered, and none that was destroyed first", len(fresh) == spawned_now, (len(fresh), spawned_now, destroyed_now))
        check("the class filter saw them too", len([c for c in actor_seen if c.endswith("/Script/Engine.Actor")]) == spawned_now, (len(actor_seen), spawned_now))
    else:
        print("every spawned actor that stayed was delivered, and none that was destroyed first: SKIP (no PyTestActor to spawn objects with)")
    check("callbacks ran on the game thread", not off_thread, len(off_thread))
    check("every delivered object was valid and an Object", not bad, bad[:3])
    check("actors are a subset of all objects", len(actor_seen) <= n_any, (len(actor_seen), n_any))
    if raised:
        check("a raising callback did not stop the other subscriptions", len(raised) == len(actor_seen), (len(raised), len(actor_seen)))
    else:
        print("note: no Actor was created during the test window; raising-callback case not exercised")

    ue4ss.unregister_notify_on_new_object(any_id)
    ue4ss.unregister_notify_on_new_object(actor_id)
    ue4ss.unregister_notify_on_new_object(raise_id)
    try:
        ue4ss.unregister_notify_on_new_object(any_id)
        check("double unregister raises", False)
    except ValueError:
        check("double unregister raises", True)

    frozen = len(seen)

    def phase_three():
        check("nothing delivered after unregister", len(seen) == frozen, (len(seen), frozen))
        print(f"summary: {frozen} objects, {len(actor_seen)} actors in the window")
        print(f"new objects finished PASS={sum(results)} FAIL={len(results) - sum(results)}")

    ue4ss.execute_with_delay(2000, phase_three)


ue4ss.execute_with_delay(5000, phase_two)
