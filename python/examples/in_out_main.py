# Calls engine functions with non-const by-reference (in/out) parameters: the updated value comes back as a result, after the
# return value and the pure outputs.
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + repr(detail)[:200] if detail else ""))


math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
strings = ue4ss.static_find_object("/Script/Engine.KismetStringLibrary")
vector = lambda x, y, z: ue4ss.make_struct("/Script/CoreUObject.Vector", X=float(x), Y=float(y), Z=float(z))

# struct in/out, no other result: the updated struct is the whole result
v = vector(9, 9, 9)
r = math.Vector_Set(v, 1.0, 2.0, 3.0)
check("Vector_Set returns the updated struct", isinstance(r, ue4ss.Struct) and (r.X, r.Y, r.Z) == (1.0, 2.0, 3.0), r and r.to_dict())
check("the struct you passed is not changed in place", (v.X, v.Y, v.Z) == (9.0, 9.0, 9.0), v.to_dict())
check("the result is owned and independent", r.is_owned() and r is not v)

n = math.Vector_Normalize(vector(3, 0, 0), 0.0001)
check("Vector_Normalize", (n.X, n.Y, n.Z) == (1.0, 0.0, 0.0), n.to_dict())

# a dict works as the struct argument too
n2 = math.Vector_Normalize({"X": 0.0, "Y": 0.0, "Z": 5.0}, 0.0001)
check("dict argument, struct result", (n2.X, n2.Y) == (0.0, 0.0) and abs(n2.Z - 1.0) < 1e-6, n2.to_dict())

# return value plus in/out: (return, in/out)
count, text = strings.ReplaceInline("hello world, hello moon", "hello", "bye", 0)
check("ReplaceInline returns (count, updated string)", (count, text) == (2, "bye world, bye moon"), (count, text))
count0, same = strings.ReplaceInline("abc", "zzz", "y", 0)
check("no match leaves the string and counts 0", (count0, same) == (0, "abc"), (count0, same))

# keywords work for in/out parameters, like any input
col = ue4ss.make_struct("/Script/CoreUObject.LinearColor", R=0.0, G=0.0, B=0.0, A=0.0)
c2 = math.LinearColor_SetRGBA(InOutColor=col, R=0.25, G=0.5, B=0.75, A=1.0)
check("LinearColor_SetRGBA by keyword", (c2.R, c2.G, c2.B, c2.A) == (0.25, 0.5, 0.75, 1.0), c2.to_dict())

# random stream seeding: in/out struct
stream = ue4ss.make_struct("/Script/CoreUObject.RandomStream")
s2 = math.SetRandomStreamSeed(stream, 1234)
check("SetRandomStreamSeed returns the stream", isinstance(s2, ue4ss.Struct), s2 and s2.to_dict())
draw1 = math.RandomIntegerFromStream(1000000, s2)
s3 = math.SetRandomStreamSeed(stream, 1234)
draw2 = math.RandomIntegerFromStream(1000000, s3)
check("same seed gives the same first draw (the in/out value really carried the seed)", draw1 == draw2 and isinstance(draw1, int), (draw1, draw2))
other = math.SetRandomStreamSeed(stream, 99)
check("a different seed differs", math.RandomIntegerFromStream(1000000, other) != draw1)

# several results: the spring interp has a return value and an in/out state
spring = ue4ss.make_struct("/Script/Engine.FloatSpringState")
res = math.FloatSpringInterp(0.0, 10.0, spring, 50.0, 1.0, 0.016, 1.0)
check("FloatSpringInterp returns (value, state)", isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], float) and isinstance(res[1], ue4ss.Struct), res)

# a const-reference parameter is an input only and is NOT returned
d = math.Vector_Distance(vector(0, 0, 0), vector(3, 4, 0))
check("const reference parameters are inputs only", d == 5.0, d)

# the in/out argument is still required and checked
for label, call, exc in (
    ("missing in/out argument", lambda: math.Vector_Set(), TypeError),
    ("wrong type for the in/out struct", lambda: math.Vector_Set(5, 1.0, 2.0, 3.0), TypeError),
):
    try:
        call()
        check(label + " refused", False)
    except exc:
        check(label + " refused", True)

print(f"in out finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
