# Every call that touches Unreal data must refuse (UEError) when it is not made on the game thread, and must never crash. All
# entry points are called from a Python worker thread here; the objects they use were obtained on the game thread.
# Needs the test game (PyTestActor). Operations that only look at wrapper state (is_valid, repr, equality) must keep working.
import threading
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


actor = ue4ss.find_first_of("PyTestActor")
if actor is None:
    print("thread guard: SKIP (needs the PyTestActor of the test game)")
    print("thread guard finished PASS=0 FAIL=0")
else:
    # --- things made on the game thread
    other = ue4ss.find_all_of("PyTestActor")[1]
    cls = actor.get_class()
    function = cls.find_function("AddInts")
    enum_type = ue4ss.static_find_object("/Script/PyTestGame.EPyColor")
    inner = actor.Inner
    arr = actor.IntArray
    fixed = actor.FixedInts
    mp = actor.NameIntMap
    st = actor.IntSet
    owned = actor.Inner.copy()
    bound = actor.AddInts
    hook_ids = ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p: None)
    repl_id = ue4ss.register_replacement("/Script/PyTestGame.PyTestActor:MakeInner", lambda c, p: ue4ss.CALL_ORIGINAL)
    notify_id = ue4ss.notify_on_new_object("/Script/Engine.Pawn", lambda o: None)
    event_id = ue4ss.bind_event(actor, "OnMulti", lambda c, p: None)
    vector_type = ue4ss.static_find_object("/Script/CoreUObject.Vector")

    # must raise UEError off the game thread (they read or write Unreal memory or call the engine)
    must_refuse = {
        "static_find_object": lambda: ue4ss.static_find_object("/Script/Engine.Actor"),
        "find_first_of": lambda: ue4ss.find_first_of("Actor"),
        "object_at_address": lambda: ue4ss.object_at_address(actor.get_address()),
        "find_all_of": lambda: ue4ss.find_all_of("Actor"),
        "find_objects": lambda: ue4ss.find_objects(class_name="Actor"),
        "make_struct": lambda: ue4ss.make_struct("/Script/CoreUObject.Vector"),
        "make_transform": lambda: ue4ss.make_transform(location=(1, 2, 3), rotation=(0, 90, 0)),
        "register_hook": lambda: ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p: None),
        "unregister_hook": lambda: ue4ss.unregister_hook(hook_ids),
        "bind_event": lambda: ue4ss.bind_event(actor, "OnMulti", lambda c, p: None),
        "unbind_event": lambda: ue4ss.unbind_event(event_id),
        "register_replacement": lambda: ue4ss.register_replacement("/Script/PyTestGame.PyTestActor:SumSet", lambda c, p: 0),
        "unregister_replacement": lambda: ue4ss.unregister_replacement(repl_id),
        "notify_on_new_object": lambda: ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda o: None),
        "unregister_notify_on_new_object": lambda: ue4ss.unregister_notify_on_new_object(notify_id),
        "UObject.get_full_name": lambda: actor.get_full_name(),
        "UObject.get_fname": lambda: actor.get_fname(),
        "UObject.get_class": lambda: actor.get_class(),
        "UObject.get_outer": lambda: actor.get_outer(),
        "UObject.get_address": lambda: actor.get_address(),
        "UObject.is_a": lambda: actor.is_a("/Script/Engine.Actor"),
        "UObject.get_property_names": lambda: actor.get_property_names(),
        "UObject.get_property_value": lambda: actor.get_property_value("Int32Value"),
        "UObject.set_property_value": lambda: actor.set_property_value("Int32Value", 1),
        "UObject.call_function": lambda: actor.call_function("AddInts", 1, 2),
        "UObject attribute read": lambda: actor.Int32Value,
        "UObject attribute write": lambda: setattr(actor, "Int32Value", 5),
        "UObject function call": lambda: actor.AddInts(1, 2),
        "BoundFunction call": lambda: bound(1, 2),
        "UClass.get_cdo": lambda: cls.get_cdo(),
        "UClass.is_child_of": lambda: cls.is_child_of("/Script/Engine.Actor"),
        "UClass.get_super": lambda: cls.get_super(),
        "UClass.get_property_names": lambda: cls.get_property_names(),
        "UClass.get_function_names": lambda: cls.get_function_names(),
        "UStruct.get_properties": lambda: cls.get_properties(),
        "UClass.find_function": lambda: cls.find_function("AddInts"),
        "UFunction.get_params": lambda: function.get_params(),
        "UFunction.get_function_flags": lambda: function.get_function_flags(),
        "UFunction.get_script": lambda: function.get_script(),
        "UFunction.make_param_block": lambda: function.make_param_block(1, 2),
        "UEnum.get_names": lambda: enum_type.get_names(),
        "UEnum.get_name_by_value": lambda: enum_type.get_name_by_value(1),
        "UEnum.get_value_by_name": lambda: enum_type.get_value_by_name("Green"),
        "Struct view attribute read": lambda: inner.Number,
        "Struct view attribute write": lambda: setattr(inner, "Number", 3),
        "Struct view to_dict": lambda: inner.to_dict(),
        "Struct view copy": lambda: inner.copy(),
        "Struct view get_property_names": lambda: inner.get_property_names(),
        "Struct view get_struct_name": lambda: inner.get_struct_name(),
        "owned Struct attribute read": lambda: owned.Number,
        "owned Struct attribute write": lambda: setattr(owned, "Number", 3),
        "owned Struct to_dict": lambda: owned.to_dict(),
        "owned Struct copy": lambda: owned.copy(),
        "Array len": lambda: len(arr),
        "Array index": lambda: arr[0],
        "Array slice": lambda: arr[1:3],
        "Array set item": lambda: arr.__setitem__(0, 1),
        "Array append": lambda: arr.append(1),
        "Array extend": lambda: arr.extend([1]),
        "Array insert": lambda: arr.insert(0, 1),
        "Array pop": lambda: arr.pop(),
        "Array clear": lambda: arr.clear(),
        "Array delete": lambda: arr.__delitem__(0),
        "Array iterate": lambda: list(arr),
        "Array to_list": lambda: arr.to_list(),
        "FixedArray len": lambda: len(fixed),
        "FixedArray index": lambda: fixed[0],
        "FixedArray set item": lambda: fixed.__setitem__(0, 1),
        "FixedArray iterate": lambda: list(fixed),
        "FixedArray to_list": lambda: fixed.to_list(),
        "Map len": lambda: len(mp),
        "Map lookup": lambda: mp["one"],
        "Map contains": lambda: "one" in mp,
        "Map set": lambda: mp.__setitem__("zz", 1),
        "Map delete": lambda: mp.__delitem__("one"),
        "Map keys": lambda: mp.keys(),
        "Map values": lambda: mp.values(),
        "Map get": lambda: mp.get("one"),
        "Map pop": lambda: mp.pop("one"),
        "Map items": lambda: mp.items(),
        "Map update": lambda: mp.update({"a": 1}),
        "Map clear": lambda: mp.clear(),
        "Map to_dict": lambda: mp.to_dict(),
        "Set len": lambda: len(st),
        "Set contains": lambda: 2 in st,
        "Set add": lambda: st.add(99),
        "Set discard": lambda: st.discard(2),
        "Set remove": lambda: st.remove(2),
        "Set update": lambda: st.update([77]),
        "Set to_set": lambda: st.to_set(),
        "Set iterate": lambda: list(st),
        "Set to_list": lambda: st.to_list(),
        "Set clear": lambda: st.clear(),
        "whole-array assignment": lambda: setattr(actor, "IntArray", [1]),
        "enum property read": lambda: actor.Color,
        "FText property read": lambda: actor.TextValue,
    }
    # must keep working off the game thread: they look only at wrapper state or are the thread-safe entry points
    must_work = {
        "UObject.is_valid": lambda: isinstance(actor.is_valid(), bool),
        "Struct view is_valid": lambda: isinstance(inner.is_valid(), bool),
        "owned Struct is_owned": lambda: owned.is_owned() is True,
        "owned Struct is_valid": lambda: owned.is_valid() is True,
        "Array is_valid": lambda: isinstance(arr.is_valid(), bool),
        "FixedArray is_valid": lambda: isinstance(fixed.is_valid(), bool),
        "Map is_valid": lambda: isinstance(mp.is_valid(), bool),
        "Set is_valid": lambda: isinstance(st.is_valid(), bool),
        "BoundFunction is_valid": lambda: isinstance(bound.is_valid(), bool),
        "repr of every wrapper": lambda: all(isinstance(repr(x), str) for x in (actor, cls, function, inner, owned, arr, mp, st, bound)),
        "equality and hash of objects": lambda: (actor == actor) and (actor != other) and isinstance(hash(actor), int),
        "is_game_thread": lambda: ue4ss.is_game_thread() is False,
        "script_generation": lambda: isinstance(ue4ss.script_generation(), int),
        "register_key_down / unregister_key_down (no Unreal data touched)": lambda: ue4ss.unregister_key_down(ue4ss.register_key_down(ue4ss.Key.F24, lambda: None)) is None,
        "set_slow_callback_warning": lambda: ue4ss.set_slow_callback_warning(50) is None,
        "euler_to_quat (pure maths)": lambda: abs(ue4ss.euler_to_quat(0, 90, 0)["Z"] - 0.7071067811865476) < 1e-9,
        "quat_to_euler (pure maths)": lambda: abs(ue4ss.quat_to_euler((0, 0, 0.7071067811865476, 0.7071067811865476))[1] - 90) < 1e-9,
        "execute_in_game_thread": lambda: ue4ss.execute_in_game_thread(lambda: None) is None,
        "execute_with_delay": lambda: ue4ss.execute_with_delay(10, lambda: None) is None,
        "call_in_game_thread returns a future": lambda: hasattr(ue4ss.call_in_game_thread(lambda: 1), "result"),
        "FName creation and text": lambda: str(ue4ss.FName("PyueThreadName")) == "PyueThreadName",
        "FName.find": lambda: ue4ss.FName.find("PyueThreadName") is not None,
        "FName.to_string": lambda: ue4ss.FName("PyueThreadName").to_string() == "PyueThreadName",
    }

    outcome = {}

    def worker():
        for name, fn in must_refuse.items():
            try:
                fn()
                outcome[name] = ("no exception", None)
            except ue4ss.UEError as e:
                outcome[name] = ("UEError", str(e))
            except Exception as e:
                outcome[name] = (type(e).__name__, str(e))
        for name, fn in must_work.items():
            try:
                outcome[name] = ("ok" if fn() else "wrong result", None)
            except Exception as e:
                outcome[name] = (type(e).__name__, str(e))

    t = threading.Thread(target=worker)
    t.start()
    t.join(60)
    check("the worker thread finished (no hang, no crash)", not t.is_alive())
    for name in must_refuse:
        kind, text = outcome.get(name, ("not run", None))
        check(f"off the game thread, {name} raises UEError", kind == "UEError" and "game thread" in (text or ""), (kind, text))
    for name in must_work:
        kind, text = outcome.get(name, ("not run", None))
        check(f"off the game thread, {name} keeps working", kind == "ok", (kind, text))

    # the game is intact afterwards and nothing was changed by the refused calls
    check("the game still answers on the game thread", actor.AddInts(2, 3) == 5)
    check("refused writes changed nothing", actor.Int32Value == 320000 and inner.Number == 7 and arr.to_list() == [3, 1, 4, 1, 5, 9, 2, 6] and len(mp) == 3 and sorted(st) == [2, 3, 5, 7, 11])
    ue4ss.unregister_hook(hook_ids)
    ue4ss.unregister_replacement(repl_id)
    ue4ss.unregister_notify_on_new_object(notify_id)
    ue4ss.unbind_event(event_id)
    print(f"thread guard finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
