# Blueprint events and functions, one by one (rqf.3/.4): for every function the chair's Blueprint classes declare, hook pre and post
# BEFORE the chair is spawned, then record what the engine calls on its own (BeginPlay etc.), what a direct call from Python triggers, what the parameters
# look like, and which functions the interactable component offers. Prints a matrix; no pass/fail.
import ue4ss
from ue4ss import engine

PATH = "/Game/Interactables/World/Seatable/LevelActor_Chair.LevelActor_Chair_C"
chair_class = engine.find(PATH).static_class()
holder = {"actor": None, "phase": "engine"}
rows = {}


def own_functions(uclass):
    seen = set()
    current = uclass
    while current is not None:
        path = current.get_full_name().split(" ", 1)[1]
        if path.startswith("/Game/"):
            for name in current.get_function_names():
                func = current.find_function(name)
                if func is None:
                    continue
                full = func.get_full_name().split(" ", 1)[1]
                if full in seen or func.get_outer().get_full_name().split(" ", 1)[1] != path:
                    continue
                seen.add(full)
                yield full, func
        current = current.get_super()


def watcher(row, kind):
    def callback(context, params, result=None):
        actor = holder["actor"]
        if actor is None or context != actor:
            row["other"] += 1
            return
        row[f"{holder['phase']}_{kind}"] += 1
        if kind == "pre" and "params" not in row:
            try:
                row["params"] = params.to_dict()
            except Exception as e:  # noqa: BLE001
                row["params"] = f"unreadable: {type(e).__name__}"
        if kind == "post" and "result" not in row and result is not None:
            row["result"] = result
    return callback


hooks = []
extra = [("/Script/Engine.Actor:ReceiveBeginPlay", None)]
for full, func in list(own_functions(chair_class)) + [(p, None) for p, _ in extra]:
    name = full.split(".")[-1].split(":")[-1]
    owner = full.split(":")[0].rsplit(".", 1)[-1]
    row = {"engine_pre": 0, "engine_post": 0, "direct_pre": 0, "direct_post": 0, "other": 0, "flags": func.get_function_flags() if func else 0}
    rows[f"{owner}::{name}"] = row
    try:
        ids = ue4ss.register_hook(full, pre=watcher(row, "pre"), post=watcher(row, "post"))
        hooks.append(ids)
    except Exception as e:  # noqa: BLE001
        row["hook_error"] = f"{type(e).__name__}: {e}"
print(f"hooked {len(hooks)} of {len(rows)} functions before spawning")

pc = ue4ss.find_first_of("PlayerController")
statics = engine.GameplayStatics
transform = engine.Transform(Rotation=engine.Quat(X=0, Y=0, Z=0, W=1), Translation=engine.Vector(X=0, Y=0, Z=3000), Scale3D=engine.Vector(X=1, Y=1, Z=1))
actor = statics.BeginDeferredActorSpawnFromClass(pc, chair_class, transform, engine.ESpawnActorCollisionHandlingMethod.AlwaysSpawn, None)
holder["actor"] = actor                      # known before FinishSpawningActor runs BeginPlay
statics.FinishSpawningActor(actor, transform)
print("spawned", actor)


def direct_calls():
    holder["phase"] = "direct"
    for name in ("ReceiveBeginPlay", "OnUsed", "UserConstructionScript"):
        func = actor.get_class().find_function(name)
        if func is None:
            print("  no function", name)
            continue
        try:
            getattr(actor, name)()
            print("  called", name, "directly")
        except Exception as e:  # noqa: BLE001
            print("  direct call of", name, "failed:", type(e).__name__, e)
    holder["phase"] = "done"
    interactable = None
    try:
        interactable = actor.Interactable
    except Exception as e:  # noqa: BLE001
        print("  no Interactable property:", type(e).__name__)
    if interactable is not None:
        print("  interactable component:", interactable)
        print("  its functions:", sorted(interactable.get_class().get_function_names())[:40])
        print("  its delegate properties:", [p["name"] for p in interactable.get_class().get_properties(inherited=True) if "Delegate" in p["type"]])
    ue4ss.execute_with_delay(500, report)


def report():
    print("matrix (counts for the spawned chair; 'other' = same function on other objects)")
    print(f"{'function':95s} eng.pre eng.post dir.pre dir.post other flags")
    for key, row in rows.items():
        print(f"{key[:95]:95s} {row['engine_pre']:7d} {row['engine_post']:8d} {row['direct_pre']:7d} {row['direct_post']:8d} {row['other']:5d} {row['flags']:#x}"
              + (f"  ERROR {row['hook_error']}" if "hook_error" in row else ""))
        if "params" in row:
            print(f"      params {row['params']}" + (f" result {row['result']}" if "result" in row else ""))
    for ids in hooks:
        ue4ss.unregister_hook(ids)
    if actor.is_valid():
        actor.K2_DestroyActor()
    print("events probe finished")


ue4ss.execute_with_delay(1500, direct_calls)
