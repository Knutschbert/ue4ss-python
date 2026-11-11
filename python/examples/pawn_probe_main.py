import ue4ss
actor_class = ue4ss.static_find_object("/Script/Engine.Actor")
print("probe: component functions:", [n for n in actor_class.get_function_names() if "omponent" in n][:20])
print("pawn probe done")
