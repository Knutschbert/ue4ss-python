# 02 - Find out what the game has: objects, classes, properties, functions.
#
# Every game has its own classes. This prints what the player controller and its pawn are made of.
import ue4ss


def describe(obj, limit=25):
    """Print the class chain, some properties and some functions of an object."""
    cls = obj.get_class()
    chain = []
    while cls is not None:                      # walk up the parents: MyPawn_C -> Character -> Pawn -> Actor -> Object
        chain.append(cls.get_fname().to_string())
        cls = cls.get_super()
    print("object:    ", obj.get_full_name())
    print("classes:   ", " -> ".join(chain))
    cls = obj.get_class()
    print("properties:", ", ".join(cls.get_property_names()[:limit]), "...")
    print("functions: ", ", ".join(cls.get_function_names()[:limit]), "...")


controller = ue4ss.find_first_of("PlayerController")     # the first live instance of the class or a subclass
if controller is None:
    print("no player controller yet: run this in a level, not during the loading screen")
else:
    describe(controller)
    pawn = controller.Pawn                                  # a property read; None while the player has no pawn
    if pawn is not None:
        describe(pawn)

# How many of the common kinds exist right now.
for name in ("Actor", "Pawn", "PlayerController", "ActorComponent"):
    print(f"{name}: {len(ue4ss.find_all_of(name))} live instances")

# Blueprint classes that are loaded right now (the game's own content).
blueprints = ue4ss.find_all_of("BlueprintGeneratedClass")
print(len(blueprints), "Blueprint classes loaded, for example:", [b.get_fname().to_string() for b in blueprints[:10]])
