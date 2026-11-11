# Who calls the simple pure Blueprint getters (one output, no inputs)? Can the callers be run from Python safely? (rqf.10). No pass/fail.
import collections

import ue4ss
from ue4ss import bytecode

PURE = 0x10000000
SKIP = 0x8000 | 0x400 | 0x100000 | 0x800
KINDS = {"BoolProperty", "IntProperty", "FloatProperty", "TextProperty", "ObjectProperty", "StructProperty", "NameProperty", "StrProperty", "ByteProperty"}

getters = {}
functions = []
for cls in ue4ss.find_all_of("BlueprintGeneratedClass"):
    path = cls.get_full_name().split(" ", 1)[1]
    if not path.startswith("/Game/"):
        continue
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None or func.get_outer() != cls:
            continue
        flags = func.get_function_flags()
        params = func.get_params()
        functions.append((f"{path}:{name}", cls, name, flags, params, func))
        if not flags & SKIP and flags & PURE and len(params) == 1 and params[0]["is_out"] and not params[0]["is_reference"] and params[0]["type"] in KINDS:
            getters[f"{path}:{name}"] = params[0]["type"]
print("getters:", len(getters), "| Blueprint functions:", len(functions))
by_name = collections.defaultdict(list)       # function name -> getter paths
for gpath in getters:
    by_name[gpath.rsplit(":", 1)[-1]].append(gpath)


def chain_paths(cls):
    out = []
    while cls is not None:
        out.append(cls.get_full_name().split(" ", 1)[1])
        cls = cls.get_super()
    return out


callers = collections.defaultdict(list)
for path, cls, name, flags, params, func in functions:
    if flags & 0x400:
        continue
    try:
        calls = bytecode.scan_calls(func)
    except bytecode.BytecodeError:
        continue
    chain = None
    for call in calls:
        target = call["target"]
        if target in getters:
            callers[path].append(target)
        elif target and ":" not in target and target in by_name and call["kind"] == "virtual":
            chain = chain or chain_paths(cls)
            for gpath in by_name[target]:
                if gpath.rsplit(":", 1)[0] in chain and gpath != path:
                    callers[path].append(gpath)
print("functions that call a getter:", len(callers))
shown = 0
for path, targets in sorted(callers.items(), key=lambda item: -len(item[1])):
    func = ue4ss.static_find_object(path)
    flags = func.get_function_flags()
    params = func.get_params()
    sig = ", ".join(f"{p['name']}:{p['type']}{'(out)' if p['is_out'] else ''}" for p in params)
    inst = len([i for i in ue4ss.find_all_of(func.get_outer().get_fname().to_string()) if i.get_class() == func.get_outer()])
    print(f"CALLER {path.rsplit('/', 1)[-1]} flags={flags:#x} ({sig}) instances={inst} calls {len(targets)} getters, e.g. {targets[0].rsplit('.', 1)[-1]} [{getters[targets[0]]}]")
    shown += 1
    if shown >= 40:
        break

natural = {}
hook_ids = []


def counter(path):
    def pre(context, params):
        natural[path] = natural.get(path, 0) + 1
    return pre


for path in callers:
    try:
        hook_ids.append(ue4ss.register_hook(path, pre=counter(path)))
    except Exception:  # noqa: BLE001
        pass
print("callers hooked:", len(hook_ids))


def report():
    for pair in hook_ids:
        ue4ss.unregister_hook(pair)
    print("callers the game called by itself:", len(natural))
    for path, n in sorted(natural.items(), key=lambda item: -item[1])[:20]:
        print(f"NATURAL {path.rsplit('/', 1)[-1]} x{n} getters: {sorted(set(t.rsplit(':', 1)[-1] + '[' + getters[t] + ']' for t in callers[path]))[:4]}")
    print("caller override probe finished")


ue4ss.execute_with_delay(5000, report)
