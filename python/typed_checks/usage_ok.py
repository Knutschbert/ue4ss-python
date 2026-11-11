"""Valid uses of the typed API: mypy must accept every line when run with the generated stubs (tools/typecheck.ps1)."""
import ue4ss
from ue4ss import engine
from ue4ss.engine import Actor, GameplayStatics, Pawn, PlayerController, Vector
from ue4ss.engine.PyTestGame import EPyColor, PyTestActor

actors: list[PyTestActor] = PyTestActor.find_all()
first = actors[0]
total: int = first.AddInts(1, 2)
swapped_a, swapped_b = first.SwapInts(3, 4)
first.IntArray.append(5)
count: int = len(first.IntArray)
fixed: int = first.FixedInts[0]
weak: Actor | None = first.WeakActor
location: Vector = first.K2_GetActorLocation()
x: float = location.X
color: EPyColor = EPyColor.Red
text: str = first.FixedStrings[0]

world = ue4ss.find_first_of("World")
controller: PlayerController | None = GameplayStatics.GetPlayerController(world, 0)
if controller is not None:
    pawn: Pawn | None = controller.Pawn
    if pawn is not None:
        pawn.K2_SetActorLocation(Vector(X=1.0, Y=2.0, Z=3.0), False, True)

if isinstance(first, Actor):
    first.K2_DestroyActor()

only_pawns = Pawn.find_all()
default = PyTestActor.default_object()
the_class = PyTestActor.static_class()
generated = engine.generate_stubs("stubs", ["Engine"])
