"""Wrong uses that the typed API must make visible: each line ends with `# E:` and the text mypy must report (checked by tools/typecheck.ps1)."""
from ue4ss.engine import Actor, GameplayStatics, Pawn, Vector
from ue4ss.engine.PyTestGame import PyTestActor

first = PyTestActor.find_all()[0]
first.AddInts("one", 2)  # E: Argument 1 to "AddInts" of "PyTestActor" has incompatible type "str"; expected "int"
wrong: str = first.AddInts(1, 2)  # E: Incompatible types in assignment (expression has type "int", variable has type "str")
first.NoSuchFunction()  # E: "PyTestActor" has no attribute "NoSuchFunction"
first.IntArray.append("text")  # E: Argument 1 to "append" of "Array" has incompatible type "str"; expected "int"
pawn: Pawn = first  # E: Incompatible types in assignment (expression has type "PyTestActor", variable has type "Pawn")
Vector(Q=1.0)  # E: Unexpected keyword argument "Q" for "Vector"
GameplayStatics.NoSuchStatic()  # E: "type[GameplayStatics]" has no attribute "NoSuchStatic"
