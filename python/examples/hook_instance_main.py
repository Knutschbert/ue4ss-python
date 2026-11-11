# register_hook(instance=obj) and Blueprint function paths (rqf.2).
# Test game: the instance filter on PyTestActor.AddInts. Chivalry 2: hook a function of a Blueprint class that is not loaded yet, filtered to one
# spawned chair (the path loads the class on demand), and check that exactly that chair's event is reported.
import ue4ss
from ue4ss import engine


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:160] + ')') if detail else ''}")


def raises(label, exc, fn):
    try:
        fn()
        check(label, False, "no exception")
    except exc:
        check(label, True)
    except Exception as e:  # noqa: BLE001
        check(label, False, repr(e))


raises("instance must be a UObject", TypeError, lambda: ue4ss.register_hook("/Script/Engine.Actor:K2_GetActorLocation", pre=lambda c, p: None, instance=5))

tbl = ue4ss.find_first_of("TBLPlayerController")
if tbl is None:
    actors = sorted(ue4ss.find_all_of("PyTestActor"), key=lambda a: a.get_fname().to_string())
    a0, a1 = actors[0], actors[1]
    seen = []
    ids_one = ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p: seen.append(("only a1", c == a1)), instance=a1)
    ids_all = ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p: seen.append(("all", c)))
    a0.AddInts(1, 2)
    a1.AddInts(3, 4)
    a0.AddInts(5, 6)
    only = [s for s in seen if s[0] == "only a1"]
    everything = [s for s in seen if s[0] == "all"]
    check("the filtered hook saw only the calls on its object", only == [("only a1", True)], only)
    check("an unfiltered hook on the same function still sees every call", len(everything) == 3, len(everything))
    ue4ss.unregister_hook(ids_one)
    ue4ss.unregister_hook(ids_all)
    a1.AddInts(1, 1)
    check("after unregistering nothing more is reported", len(seen) == 4, len(seen))
    print("hook instance finished")
else:
    base = "/Game/Interactables/World/Seatable/LevelActor_BaseSeatable.LevelActor_BaseSeatable_C"
    unloaded = ue4ss.static_find_object(base) is None
    print("the Blueprint class was not loaded before the hook:", unloaded)
    events = []
    ids = ue4ss.register_hook(base + ":ExecuteUbergraph_LevelActor_BaseSeatable", pre=lambda c, p: events.append(("pre", p.EntryPoint)))
    check("a Blueprint function path loads its class and hooks it", ue4ss.static_find_object(base) is not None and len(ids) == 2)
    pc = ue4ss.find_first_of("PlayerController")
    chair_class = engine.find("/Game/Interactables/World/Seatable/LevelActor_Chair.LevelActor_Chair_C")
    transform = engine.Transform(Rotation=engine.Quat(X=0, Y=0, Z=0, W=1), Translation=engine.Vector(X=0, Y=0, Z=3000), Scale3D=engine.Vector(X=1, Y=1, Z=1))
    chairs = []
    for z in (3000, 3500):
        transform.Translation.Z = z
        chair = engine.GameplayStatics.BeginDeferredActorSpawnFromClass(pc, chair_class.static_class(), transform, engine.ESpawnActorCollisionHandlingMethod.AlwaysSpawn, None)
        engine.GameplayStatics.FinishSpawningActor(chair, transform)
        chairs.append(chair)
    check("two chairs spawned", len(chairs) == 2 and all(c is not None for c in chairs))
    # a Blueprint class that is not loaded yet: hooking one of its functions by path must bring the class in
    candidates = ["LevelActor_Throne", "LevelActor_Outhouse02", "LevelActor_RoyalBench", "LevelActor_Stocks", "LevelActor_FloggingPole", "LevelActor_Bench_04"]
    done = False
    for name in candidates:
        fresh_path = f"/Game/Interactables/World/Seatable/{name}.{name}_C"
        if ue4ss.static_find_object(fresh_path) is not None:
            continue
        for function in (f"ExecuteUbergraph_{name}", "UserConstructionScript", "ReceiveBeginPlay"):
            try:
                fresh_ids = ue4ss.register_hook(f"{fresh_path}:{function}", pre=lambda c, p: None)
            except ue4ss.UEError:
                continue
            check(f"{name} was not loaded, and hooking {function} loaded it", ue4ss.static_find_object(fresh_path) is not None and len(fresh_ids) == 2)
            ue4ss.unregister_hook(fresh_ids)
            done = True
            break
        if done:
            break
    if not done:
        print("SKIP loading a Blueprint class by hooking it: no unloaded candidate had a hookable function", candidates)
    watched = []
    ids_one = ue4ss.register_hook("/Script/Engine.Actor:K2_GetActorLocation", pre=lambda c, p: watched.append(c), instance=chairs[0])
    chairs[0].K2_GetActorLocation()
    chairs[1].K2_GetActorLocation()
    chairs[1].K2_GetActorLocation()
    check("the filtered hook saw the chair it was bound to", chairs[0] in watched, len(watched))
    check("and none of the other chair's calls (nor of the thousands of other actors)", all(c == chairs[0] for c in watched), len(watched))

    def finish():
        check("the unfiltered Blueprint hook saw the BeginPlay event of the chairs", len(events) >= 2, len(events))
        for ids_ in (ids, ids_one):
            ue4ss.unregister_hook(ids_)
        for c in chairs:
            if c is not None and c.is_valid():
                c.K2_DestroyActor()
        print("hook instance finished")

    ue4ss.execute_with_delay(1500, finish)
