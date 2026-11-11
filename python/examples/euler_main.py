# Euler angles against the engine's own conversions: euler_to_quat / quat_to_euler versus KismetMathLibrary.Quat_MakeFromEuler and
# Quat_Rotator for random angles, quaternion fields accepting rotations, and a spawned actor that ends up rotated as asked.
import random
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:220]) if (detail != "" and not ok) else ""))


def angle_gap(a, b):
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


math_lib = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
statics = ue4ss.static_find_object("/Script/Engine.GameplayStatics")
world = ue4ss.find_first_of("World")
mesh_actor = ue4ss.static_find_object("/Script/Engine.StaticMeshActor")
rng = random.Random(2026)

# 1. euler_to_quat == the engine, for random angles (engine Euler vector is (Roll, Pitch, Yaw))
worst = 0.0
for _ in range(200):
    pitch, yaw, roll = rng.uniform(-180, 180), rng.uniform(-180, 180), rng.uniform(-180, 180)
    ours = ue4ss.euler_to_quat(pitch, yaw, roll)
    engine = math_lib.Quat_MakeFromEuler({"X": roll, "Y": pitch, "Z": yaw})
    dot = abs(ours["X"] * engine.X + ours["Y"] * engine.Y + ours["Z"] * engine.Z + ours["W"] * engine.W)
    worst = max(worst, 1.0 - dot)
check("euler_to_quat matches the engine's Quat_MakeFromEuler for 200 random angles", worst < 1e-6, worst)

# 2. quat_to_euler == the engine's Quat_Rotator, away from the poles
worst = 0.0
for _ in range(200):
    q = ue4ss.euler_to_quat(rng.uniform(-85, 85), rng.uniform(-180, 180), rng.uniform(-180, 180))
    mine = ue4ss.quat_to_euler(q)
    rotator = math_lib.Quat_Rotator({"X": q["X"], "Y": q["Y"], "Z": q["Z"], "W": q["W"]})
    worst = max(worst, angle_gap(mine[0], rotator.Pitch), angle_gap(mine[1], rotator.Yaw), angle_gap(mine[2], rotator.Roll))
check("quat_to_euler matches the engine's Quat_Rotator for 200 random rotations", worst < 2e-3, worst)

for pole in (90.0, -90.0):
    q = ue4ss.euler_to_quat(pole, 40, 25)
    mine = ue4ss.quat_to_euler(q)
    rotator = math_lib.Quat_Rotator(q)
    check(f"at the pole (pitch {pole:+.0f}) the pitch agrees with the engine", angle_gap(mine[0], rotator.Pitch) < 0.05, (mine, rotator.to_dict()))

# 3. a quaternion field accepts every spelling of the same rotation (FTransform.Rotation is an FQuat)
reference = ue4ss.make_transform(rotation=(10, 20, 30)).Rotation
want = (reference.X, reference.Y, reference.Z, reference.W)
rotator_struct = ue4ss.make_struct("/Script/CoreUObject.Rotator", Pitch=10.0, Yaw=20.0, Roll=30.0)
spellings = {"tuple": (10, 20, 30), "list": [10, 20, 30], "dict": {"Pitch": 10, "Yaw": 20, "Roll": 30}, "dict, any case": {"pitch": 10, "YAW": 20, "Roll": 30},
             "Rotator struct": rotator_struct, "quaternion dict": ue4ss.euler_to_quat(10, 20, 30)}
for name, value in spellings.items():
    transform = ue4ss.make_struct("/Script/CoreUObject.Transform")
    transform.Rotation = value
    got = (transform.Rotation.X, transform.Rotation.Y, transform.Rotation.Z, transform.Rotation.W)
    check(f"assigning {name} to a quaternion field", all(abs(a - b) < 1e-6 for a, b in zip(got, want)), got)
transform = ue4ss.make_struct("/Script/CoreUObject.Transform")
transform.Rotation = {"Yaw": 90}
check("missing keys count as 0", abs(transform.Rotation.Z - 0.70710678) < 1e-6 and abs(transform.Rotation.X) < 1e-6)
for label, bad, exc in (("two angles", (1, 2), ValueError), ("mixed key sets", {"Pitch": 1, "X": 2}, ValueError), ("text", ("a", 0, 0), TypeError), ("unknown key", {"Pitch": 1, "Banana": 1}, ValueError)):
    try:
        transform.Rotation = bad
        check(f"a bad rotation ({label}) is refused", False)
    except exc:
        check(f"a bad rotation ({label}) is refused", True)

# 4. make_transform in a call: spawn rotated actors and read the rotation back from the engine
for pitch, yaw, roll in ((0, 90, 0), (0, -45, 0), (30, 120, -20), (-60, 10, 75)):
    transform = ue4ss.make_transform(location=(0, 0, 500), rotation=(pitch, yaw, roll))
    actor = statics.BeginDeferredActorSpawnFromClass(world, mesh_actor, transform, 1, None)
    statics.FinishSpawningActor(actor, transform)
    rotation = actor.K2_GetActorRotation()
    gap = max(angle_gap(rotation.Pitch, pitch), angle_gap(rotation.Yaw, yaw), angle_gap(rotation.Roll, roll))
    check(f"a spawned actor faces ({pitch}, {yaw}, {roll})", gap < 1e-2, rotation.to_dict())
    actor.K2_DestroyActor()

# spawn with the rotation written straight into a transform dict, no helper
spawn = {"Rotation": {"Yaw": 90}, "Translation": {"X": 0, "Y": 0, "Z": 500}, "Scale3D": {"X": 1, "Y": 1, "Z": 1}}
actor = statics.BeginDeferredActorSpawnFromClass(world, mesh_actor, spawn, 1, None)
statics.FinishSpawningActor(actor, spawn)
check("a transform dict with {'Rotation': {'Yaw': 90}} spawns a rotated actor", angle_gap(actor.K2_GetActorRotation().Yaw, 90) < 1e-2, actor.K2_GetActorRotation().to_dict())
actor.K2_DestroyActor()

# 5. other orders, checked against engine single-axis rotations and the identity with the unreal order
x90 = ue4ss.euler_to_quat(90, 0, 0, order="xyz")
engine_roll = math_lib.Quat_MakeFromEuler({"X": -90, "Y": 0, "Z": 0})   # roll about X is negated in Unreal's convention
check("an x-axis rotation of the 'xyz' order equals the engine's roll with the opposite sign", abs(x90["X"] - engine_roll.X) < 1e-6 and abs(x90["W"] - engine_roll.W) < 1e-6, (x90, engine_roll.to_dict()))
a = ue4ss.euler_to_quat(25, 70, -15)
b = ue4ss.euler_to_quat(70, -25, 15, order="zyx")
check("'unreal' equals 'zyx' with (yaw, -pitch, -roll)", all(abs(a[k] - b[k]) < 1e-9 for k in "XYZW"))
back = ue4ss.quat_to_euler(ue4ss.euler_to_quat(10, 20, 30, order="yxz"), order="yxz")
check("every order round-trips through quat_to_euler", all(abs(x - y) < 1e-6 for x, y in zip(back, (10, 20, 30))), back)

print(f"euler finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
