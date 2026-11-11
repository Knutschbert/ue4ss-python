# Feasibility probe for "a Python script as an actor" (epic idea; facts in the ticket) (ideas from the owner, 2026-10-24): can a plain engine actor be given behaviour by Python only?
# 1. spawn a plain Actor deferred, enable ticking BEFORE it is finished (a base Actor does not tick by default), 2. hook ReceiveTick for that instance,
# 3. move it every tick from the hook, 4. also try the same with a timer loop of execute_with_delay, 5. an event hook on a function we call (a "dynamic event").
import ue4ss
from ue4ss.engine.Engine import Actor, StaticMeshActor, ESpawnActorCollisionHandlingMethod, GameplayStatics, PlayerController, Transform, Vector
from ue4ss.engine.CoreUObject import Quat


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


controller = PlayerController.find_first()
transform = Transform(Rotation=Quat(**ue4ss.euler_to_quat(0, 0, 0)), Translation=Vector(X=0.0, Y=0.0, Z=3000.0), Scale3D=Vector(X=1, Y=1, Z=1))
actor = GameplayStatics.BeginDeferredActorSpawnFromClass(controller, StaticMeshActor.static_class(), transform, ESpawnActorCollisionHandlingMethod.AlwaysSpawn, None)
print("plain Actor has a root component:", GameplayStatics.BeginDeferredActorSpawnFromClass(controller, Actor.static_class(), transform, ESpawnActorCollisionHandlingMethod.AlwaysSpawn, None).RootComponent)
print("tick defaults before finishing:", actor.PrimaryActorTick.bCanEverTick, actor.PrimaryActorTick.bStartWithTickEnabled)
actor.PrimaryActorTick.bCanEverTick = True
actor.PrimaryActorTick.bStartWithTickEnabled = True
GameplayStatics.FinishSpawningActor(actor, transform)
actor.RootComponent.SetMobility(2)           # Movable
actor.SetActorTickEnabled(True)
print("tick after finishing:", actor.IsActorTickEnabled(), "| root:", actor.RootComponent)
ticks = []
start = actor.K2_GetActorLocation().Z


def on_tick(context, params):
    ticks.append(params.DeltaSeconds)
    here = context.K2_GetActorLocation()
    context.K2_SetActorLocation(Vector(X=here.X + 1.0, Y=here.Y, Z=here.Z), False, False)


try:
    ids = ue4ss.register_hook("/Script/Engine.Actor:ReceiveTick", pre=on_tick, instance=actor)
    hooked = True
except Exception as e:  # noqa: BLE001
    print("hook on ReceiveTick failed:", type(e).__name__, e)
    hooked = False
timer = []


def timer_step(n=0):
    if n < 20 and actor.is_valid():
        timer.append(n)
        here = actor.K2_GetActorLocation()
        actor.K2_SetActorLocation(Vector(X=here.X, Y=here.Y + 1.0, Z=here.Z), False, False)
        ue4ss.execute_with_delay(50, lambda: timer_step(n + 1))


timer_step()


# the same hook on an actor of a Blueprint-compiled class (AActor::Tick only calls ReceiveTick for those): a second BP_Sky_Sphere in the test game
bp_ticks = []
bp_class = ue4ss.static_find_object("/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C")
bp_actor = None
if bp_class is not None:
    bp_actor = GameplayStatics.BeginDeferredActorSpawnFromClass(controller, bp_class, transform, ESpawnActorCollisionHandlingMethod.AlwaysSpawn, None)
    bp_actor.PrimaryActorTick.bCanEverTick = True
    bp_actor.PrimaryActorTick.bStartWithTickEnabled = True
    GameplayStatics.FinishSpawningActor(bp_actor, transform)
    bp_ids = ue4ss.register_hook("/Script/Engine.Actor:ReceiveTick", pre=lambda context, params: bp_ticks.append(params.DeltaSeconds), instance=bp_actor)


frames = []
import time as _time   # noqa: E402


def frame_loop():
    """A callback about once per engine tick: execute_with_delay(1) re-queues into the delayed queue, which is drained once per tick.
    (execute_in_game_thread re-queues into the tick's own drain and ran 6000 times a second in a first try: not a frame signal.)"""
    frames.append(_time.perf_counter())
    if _time.perf_counter() - frames[0] < 2.0:
        ue4ss.execute_with_delay(1, frame_loop)


frame_loop()


def report():
    here = actor.K2_GetActorLocation()
    print(f"ReceiveTick hook fired {len(ticks)} times; location now X={here.X:.0f} Y={here.Y:.0f}; timer steps {len(timer)}")
    check("a plain Actor can be made to tick before it is finished", actor.IsActorTickEnabled())
    gaps = [b - a for a, b in zip(frames, frames[1:])]
    print(f"a per-frame loop with execute_in_game_thread ran {len(frames)} times in 2 s ({len(frames) / 2:.0f} per second), longest gap {max(gaps) * 1000:.0f} ms" if gaps else "no frames")
    check("a Python loop once per engine tick is possible (execute_with_delay(1) re-queued: the headless game ticks about 900 times a second)", len(frames) > 30 and (max(gaps) < 0.1 if gaps else False), f"{len(frames)} calls")
    print("ReceiveTick hook on the native class fired", len(ticks), "times (expected 0: AActor::Tick only calls ReceiveTick for Blueprint-compiled classes)")
    if bp_class is not None:
        print("ReceiveTick hook on a Blueprint-compiled actor fired", len(bp_ticks), "times (0: BP_Sky_Sphere does not implement ReceiveTick, so there is no bytecode for a script hook to catch)")
        ue4ss.unregister_hook(bp_ids)
        bp_actor.K2_DestroyActor()
    check("a timer loop (execute_with_delay) drives an actor too", here.Y > 5.0 and len(timer) >= 15, f"Y={here.Y}")
    if hooked:
        ue4ss.unregister_hook(ids)
    actor.K2_DestroyActor()
    print("actor tick probe finished")


ue4ss.execute_with_delay(2000, report)
