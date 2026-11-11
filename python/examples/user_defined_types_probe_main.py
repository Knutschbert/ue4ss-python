# What are the Blueprint-made enums and structs that properties refer to, and how can they be found? No pass/fail.
import ue4ss

for path in ("/Game/UI/Blueprints/CinematicMenuMode.CinematicMenuMode", "/Game/UI/WidgetsFrontend/Quests/QuestCriteriaEntryData.QuestCriteriaEntryData",
             "/Game/UI/WidgetsIngame/killFeed/KillFeedStyle.KillFeedStyle"):
    obj = ue4ss.static_find_object(path)
    print(path.rsplit("/", 1)[-1], "->", obj, "| class:", obj.get_class().get_full_name() if obj else None, "| type:", type(obj).__name__ if obj else None)
for name in ("UserDefinedEnum", "UserDefinedStruct", "Enum", "ScriptStruct"):
    exact = ue4ss.find_objects(class_name=name, exact_class=True)
    print(name, "exact:", len(exact), "| /Game ones:", len([o for o in exact if "/Game/" in o.get_full_name()]), "| non-exact:", len(ue4ss.find_objects(class_name=name)))
print("user defined probe finished")
