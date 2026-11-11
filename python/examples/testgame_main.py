# Checks every property kind and call shape against the populated PyTestActor objects of the PyTestGame test game
# (testgame/PyTestGame). Run with: python3 -I tools/testgame.py run python/examples/testgame_main.py "testgame checks done"
import enum
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
check("three populated test actors", len(actors) == 3, [a.get_fname().to_string() for a in actors])
a0, a1, a2 = actors
names = lambda objs: [o.get_fname().to_string() for o in objs]
strs = lambda items: [str(i) for i in items]

# ---- scalars and bit-fields
check("bool", a0.bPlain is True)
check("bit-fields", a0.bFlagA is True and a0.bFlagB is False, (a0.bFlagA, a0.bFlagB))
check("int8..int64", (a0.Int8Value, a0.Int16Value, a0.Int32Value, a0.Int64Value) == (-8, -1600, 320000, -6400000000000), (a0.Int8Value, a0.Int16Value, a0.Int32Value, a0.Int64Value))
check("uint8..uint64", (a0.UInt8Value, a0.UInt16Value, a0.UInt32Value, a0.UInt64Value) == (200, 60000, 4000000000, 18000000000000000000))
check("float and double", a0.FloatValue == 1.5 and a0.DoubleValue == 2.718281828459045, (a0.FloatValue, a0.DoubleValue))

# ---- enums: enum class byte, old-style TEnumAsByte, wide enum class
check("enum class (byte) is an IntEnum", isinstance(a0.Color, enum.IntEnum) and a0.Color.name == "Green" and a0.Color == 1, a0.Color)
check("TEnumAsByte is an IntEnum", isinstance(a0.Mode, enum.IntEnum) and a0.Mode.name == "PyMode_Active" and a0.Mode == 1, a0.Mode)
check("int32 enum class is an IntEnum", isinstance(a0.Wide, enum.IntEnum) and a0.Wide.name == "Big" and a0.Wide == 100000, a0.Wide)
old_color = a0.Color
a0.Color = type(a0.Color).Blue
check("enum write by member", a0.Color.name == "Blue")
a0.Color = 1
check("enum write by int, restored", a0.Color == old_color)

# ---- names and text
check("FName, FString, FText", (str(a0.NameValue), a0.StringValue, a0.TextValue) == ("PyTestName", "py test string", "py test text"), (a0.NameValue, a0.StringValue, a0.TextValue))
a0.TextValue = "changed text"
check("FText write", a0.TextValue == "changed text")
a0.TextValue = "py test text"

# ---- object references
check("raw object, actor, class refs", a0.RawObject == a0 and a0.ActorRef == a1 and a0.ClassRef.get_fname().to_string() == "Actor", (a0.RawObject, a0.ActorRef, a0.ClassRef))
check("subclass-of", a0.SubClass.get_fname().to_string() == "PyTestActor", a0.SubClass)
check("weak pointer resolves to its actor", a0.WeakActor == a0 and a1.WeakActor == a0, (a0.WeakActor, a1.WeakActor))
a1.WeakActor = a2
check("weak pointer write", a1.WeakActor == a2)
a1.WeakActor = None
check("weak pointer cleared", a1.WeakActor is None)
a1.WeakActor = a0
check("weak pointer restored", a1.WeakActor == a0)
raises("weak pointer rejects another class", ue4ss.UEError, lambda: setattr(a1, "WeakActor", ue4ss.static_find_object("/Script/Engine.Actor")))
check("lazy pointer resolves to its actor", a0.LazyActor == a1, a0.LazyActor)
original_lazy = a0.LazyActor
a0.LazyActor = a2
check("lazy pointer write", a0.LazyActor == a2, a0.LazyActor)
a0.LazyActor = None
check("lazy pointer cleared", a0.LazyActor is None)
a0.LazyActor = original_lazy
check("lazy pointer restored", a0.LazyActor == original_lazy)
raises("lazy pointer rejects another class", ue4ss.UEError, lambda: setattr(a0, "LazyActor", ue4ss.static_find_object("/Script/Engine.Actor")))
check("soft object and soft class are path strings", a0.SoftObject == "/Engine/Maps/Entry.Entry" and a0.SoftClass == "/Script/Engine.Actor", (a0.SoftObject, a0.SoftClass))
a0.SoftObject = "/Game/Other.Other"
check("soft write", a0.SoftObject == "/Game/Other.Other")
a0.SoftObject = "/Engine/Maps/Entry.Entry"
check("interface reference reads as its object", a0.InterfaceRef == a0, a0.InterfaceRef)
raises("interface reference is read-only", ue4ss.UEError, lambda: setattr(a0, "InterfaceRef", a1))

# ---- structs
check("FVector", (a0.VectorValue.X, a0.VectorValue.Y, a0.VectorValue.Z) == (1.0, 2.0, 3.0))
inner = a0.Inner
check("nested struct fields", (inner.Number, inner.Label, str(inner.Tag)) == (7, "inner label", "InnerTag"), inner.to_dict())
check("weak pointer inside a struct", inner.Weak == a0, inner.Weak)
check("array and map inside a struct", inner.Numbers.to_list() == [10, 20, 30] and {str(k).lower(): v for k, v in inner.Weights.items()} == {"a": 0.5, "b": 1.5}, (inner.Numbers.to_list(), inner.Weights.to_dict()))
d = inner.to_dict()
check("to_dict turns containers into plain values", d["Numbers"] == [10, 20, 30] and d["Weights"] and d["Label"] == "inner label", d)

# ---- arrays of every element kind
check("TArray<int>", a0.IntArray.to_list() == [3, 1, 4, 1, 5, 9, 2, 6])
check("TArray<float>", a0.FloatArray.to_list() == [0.5, 1.5, 2.5])
check("TArray<bool>", a0.BoolArray.to_list() == [True, False, True])
check("TArray<FString>", a0.StringArray.to_list() == ["alpha", "beta", "gamma"])
check("TArray<FName>", strs(a0.NameArray.to_list()) == ["NameA", "NameB"])
check("TArray<FText>", a0.TextArray.to_list() == ["text one", "text two"])
check("TArray<AActor*>", names(a0.ActorArray.to_list()) == ["PyTestActor_0", "PyTestActor_1", "PyTestActor_2"])
check("TArray<struct>", [s.Number for s in a0.StructArray] == [100, 101, 102] and [s.Label for s in a0.StructArray] == ["item 0", "item 1", "item 2"])
check("TArray<FVector>", [(v.X, v.Y, v.Z) for v in a0.VectorArray] == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])


def cycle(arr, sample):
    before = [x.to_dict() if isinstance(x, ue4ss.Struct) else x for x in arr.to_list()]
    arr.append(sample)
    arr.insert(0, sample)
    arr.extend([sample])
    arr.pop()
    del arr[0]
    arr.pop()
    return before == [x.to_dict() if isinstance(x, ue4ss.Struct) else x for x in arr.to_list()]


check("resize cycle on float, bool, str, FName, FText, object, struct arrays",
      all((cycle(a0.FloatArray, 9.5), cycle(a0.BoolArray, True), cycle(a0.StringArray, "zz"), cycle(a0.NameArray, "NewName"), cycle(a0.TextArray, "new text"),
           cycle(a0.ActorArray, a2), cycle(a0.StructArray, a0.StructArray[0]))))
a0.FloatArray[1] = 7.25
check("float element write", a0.FloatArray[1] == 7.25)
a0.FloatArray[1] = 1.5
a0.StringArray[0] = "ALPHA"
check("string element write", a0.StringArray[0] == "ALPHA")
a0.StringArray[0] = "alpha"
a0.IntArray = [1, 2, 3]
check("whole-array assignment", a0.IntArray.to_list() == [1, 2, 3])
a0.IntArray = [3, 1, 4, 1, 5, 9, 2, 6]

# ---- maps
nm = a0.NameIntMap
check("TMap<FName,int> reads", len(nm) == 3 and nm["two"] == 2 and "three" in nm and nm.get("zzz", -1) == -1, nm.to_dict())
nm["four"] = 4
del nm["one"]
check("map write and delete", nm["four"] == 4 and "one" not in nm and len(nm) == 3)
nm["one"] = 1
del nm["four"]
check("map restored", {str(k).lower(): v for k, v in nm.items()} == {"one": 1, "two": 2, "three": 3}, nm.to_dict())
sm = a0.StringMap
check("TMap<FString,FString>", sm["k1"] == "v1" and len(sm) == 2)
sm["k3"] = "v3"
sm.pop("k3")
check("string map write/pop", "k3" not in sm and len(sm) == 2)
am = a0.IntActorMap
check("TMap<int,AActor*>", am[1] == a1 and len(am) == 3, am.to_dict())
am[7] = a2
check("map of objects write", am[7] == a2)
del am[7]
stm = a0.StructMap
entry = stm["entry"]
check("TMap<FName,struct>", entry.Number == 5 and entry.Label == "map entry" and entry.is_owned())
entry.Number = 6
stm["entry"] = entry
check("struct value write-back", stm["entry"].Number == 6)
entry.Number = 5
stm["entry"] = entry

# ---- sets (the tickets that needed populated engine sets)
iset = a0.IntSet
check("TSet<int> reads", sorted(iset) == [2, 3, 5, 7, 11] and 7 in iset and 4 not in iset and len(iset) == 5, sorted(iset))
iset.add(13)
iset.add(13)
iset.discard(2)
check("set add (twice) and discard", sorted(iset) == [3, 5, 7, 11, 13], sorted(iset))
iset.update([17, 19])
iset.remove(17)
check("set update and remove", sorted(iset) == [3, 5, 7, 11, 13, 19], sorted(iset))
raises("set remove of a missing value raises KeyError", KeyError, lambda: iset.remove(999))
a0.IntSet = {2, 3, 5, 7, 11}
check("whole-set assignment restores", sorted(a0.IntSet) == [2, 3, 5, 7, 11])
nset = a0.NameSet
nset.add("sc")
check("TSet<FName> add", "sc" in nset and len(nset) == 3)
nset.discard("sc")
sset = a0.StringSet
check("TSet<FString>", sorted(sset) == ["s1", "s2", "s3"])
sset.add("s4")
sset.discard("s4")
aset = a0.ActorSet
check("TSet<AActor*> reads (the populated object set)", len(aset) == 3 and a1 in aset and a2 in aset, names(aset))
aset.discard(a2)
check("object set discard", a2 not in aset and len(aset) == 2)
aset.add(a2)
check("object set add back", a2 in aset and len(aset) == 3)
aset.clear()
check("object set clear", len(aset) == 0)
aset.update([a0, a1, a2])
check("object set update restores", len(aset) == 3 and a0 in aset)

# ---- fixed-size arrays (ArrayDim > 1)
fixed = a0.FixedInts
check("fixed-size int array is a FixedArray", isinstance(fixed, ue4ss.FixedArray) and len(fixed) == 4, repr(fixed))
check("fixed int array reads", fixed.to_list() == [11, 22, 33, 44] and fixed[-1] == 44 and fixed[1:3] == [22, 33], fixed.to_list())
check("fixed int array iterates and tests membership", list(fixed) == [11, 22, 33, 44] and 33 in fixed)
fixed[1] = 5
check("fixed int array item assignment", fixed.to_list() == [11, 5, 33, 44])
check("fixed array other elements untouched, not the neighbour property", a0.FixedStrings.to_list() == ["fixed zero", "fixed one"])
a0.FixedInts = [1, 2, 3, 4]
check("fixed array whole assignment", fixed.to_list() == [1, 2, 3, 4])
raises("fixed array index out of range", IndexError, lambda: fixed[4])
raises("fixed array assignment index out of range", IndexError, lambda: fixed.__setitem__(4, 1))
raises("fixed array wrong length", ValueError, lambda: setattr(a0, "FixedInts", [1, 2, 3]))
raises("fixed array too many items", ValueError, lambda: setattr(a0, "FixedInts", [1, 2, 3, 4, 5]))
raises("fixed array bad item type", TypeError, lambda: fixed.__setitem__(0, "x"))
raises("fixed array overflow", OverflowError, lambda: fixed.__setitem__(0, 2 ** 31))
raises("fixed array cannot shrink", TypeError, lambda: fixed.__delitem__(0))
raises("fixed array slice assignment refused", TypeError, lambda: fixed.__setitem__(slice(0, 2), [1, 2]))
check("a refused write changed nothing", fixed.to_list() == [1, 2, 3, 4])
a0.FixedInts = [11, 22, 33, 44]
strings = a0.FixedStrings
strings[0] = "h\u00e9llo \U0001F600"
check("fixed FString array write and read", strings.to_list() == ["h\u00e9llo \U0001F600", "fixed one"], strings.to_list())
strings[0] = "fixed zero"
bytes_ = a0.FixedBytes
check("fixed byte array reads", bytes_.to_list() == [7, 8, 9], bytes_.to_list())
bytes_[1] = 255
check("fixed byte array write leaves the neighbours", bytes_.to_list() == [7, 255, 9], bytes_.to_list())
raises("fixed byte array range check", OverflowError, lambda: bytes_.__setitem__(0, 256))
bytes_[1] = 8
vectors = a0.FixedVectors
check("fixed struct array reads views", len(vectors) == 2 and vectors[1].Z == 6.0 and vectors[0].to_dict() == {"X": 1.0, "Y": 2.0, "Z": 3.0}, vectors[0].to_dict())
vectors[0].X = 10.0
check("a struct element view writes in place", a0.FixedVectors[0].X == 10.0 and vectors[1].X == 4.0)
vectors[1] = ue4ss.make_struct("/Script/CoreUObject.Vector", X=7.0, Y=8.0, Z=9.0)
check("assigning a Struct copies it into the element", vectors[1].to_dict() == {"X": 7.0, "Y": 8.0, "Z": 9.0} and vectors[0].X == 10.0, vectors[1].to_dict())
raises("fixed struct array refuses a dict", TypeError, lambda: vectors.__setitem__(0, {"X": 1.0}))
vectors[0] = ue4ss.make_struct("/Script/CoreUObject.Vector", X=1.0, Y=2.0, Z=3.0)
vectors[1] = ue4ss.make_struct("/Script/CoreUObject.Vector", X=4.0, Y=5.0, Z=6.0)
check("repr names the array", repr(fixed).startswith("<FixedArray 'FixedInts' of 4 "), repr(fixed))

# ---- delegates (read only) and their effect
single = a0.OnSingle
check("single-cast delegate reads as (object, FName)", single is not None and single[0] == a0 and str(single[1]) == "OnHandled", single)
multi = a1.OnMulti
check("multicast delegate lists its bindings", {(o.get_fname().to_string(), str(f)) for o, f in multi} == {("PyTestActor_1", "OnHandled"), ("PyTestActor_0", "OnHandled")}, multi)
raises("delegates are read-only", ue4ss.UEError, lambda: setattr(a0, "OnSingle", None))

# ---- calls
check("simple call", a0.AddInts(2, 3) == 5)
check("in/out parameters return their new values", a0.SwapInts(1, 2) == (2, 1))
check("outputs come back as a tuple", a0.SplitVector({"X": 1.0, "Y": 2.0, "Z": 3.0}) == (1.0, 2.0, 3.0))
rng = a0.Range(4)
check("array return value", isinstance(rng, ue4ss.Array) and rng.to_list() == [0, 1, 2, 3], rng)
check("array of names in, string out", a0.JoinNames(["PyNameX", "PyNameY", "PyNameZ"], "-") == "PyNameX-PyNameY-PyNameZ", a0.JoinNames(["PyNameX", "PyNameY", "PyNameZ"], "-"))
mi = a0.MakeInner(9, "made")
check("struct return value", mi.Number == 9 and mi.Label == "made" and mi.is_owned())
check("set argument", a0.SumSet({1, 2, 3, 4}) == 10)
check("object return value", a0.PickOther(1) == a1 and a0.PickOther(9) is None)
check("interface function (BlueprintNativeEvent)", a0.Ping(41) == 42)
before = a0.HandledCount
a0.OnHandled(77)
check("function bound to a delegate runs", a0.LastHandled == 77 and a0.HandledCount == before + 1)

# ---- a function that faults on purpose: UEError, and the game carries on
faults = ue4ss._selftest_faults_survived()
raises("a faulting engine function becomes UEError", ue4ss.UEError, lambda: a0.FaultNow())
check("the fault was counted and the game still answers", ue4ss._selftest_faults_survived() == faults + 1 and a0.AddInts(1, 1) == 2)

# ---- everything we changed is back to its original value
check("state restored", a0.Color == old_color and a0.TextValue == "py test text" and sorted(a0.IntSet) == [2, 3, 5, 7, 11] and a1.WeakActor == a0)

print(f"testgame checks done PASS={sum(results)} FAIL={len(results) - sum(results)}")
