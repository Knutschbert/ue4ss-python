# DefaultLoadingScreenPanelWidget (a Blueprint derived from ULoadingScreenPanelWidget, tip from the owner): is its class loaded, what do its
# functions look like, is there a live instance? (rqf.4). Prints a report; no pass/fail.
import ue4ss

classes = [c for c in ue4ss.find_all_of("BlueprintGeneratedClass") if "LoadingScreen" in c.get_full_name()]
print("Blueprint classes with LoadingScreen in the name:", len(classes))
for cls in classes:
    path = cls.get_full_name().split(" ", 1)[1]
    parent = cls.get_super()
    print(f"CLASS {path}  super={parent.get_full_name().split(' ', 1)[1] if parent else None}")
    instances = [i for i in ue4ss.find_all_of(cls.get_fname().to_string()) if i.get_class() == cls]
    print(f"   live instances: {len(instances)}")
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None:
            continue
        owner = func.get_outer().get_full_name().split(" ", 1)[1]
        sig = ", ".join(f"{p['name']}:{p['type']}{'(out)' if p['is_out'] else ''}{'(ret)' if p['is_return'] else ''}" for p in func.get_params())
        print(f"   {name} flags={func.get_function_flags():#x} owner={owner.rsplit('.', 1)[-1]} ({sig})")
print("loading screen probe finished")
