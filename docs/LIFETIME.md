# UObject lifetime: handle design

Decision record. Everything under "Facts" was read from RE-UE4SS
97adba0 / UEPseudo fbb4fdd; "Open questions" were not verified at the time of writing; what was checked
later is under "Findings from real games".

## Problem
A Python wrapper outlives the Unreal object it points at. After a level change or GC the raw
`UObject*` dangles, and using it crashes the game. Every access must detect that and raise
`InvalidObjectError` instead.

## Facts
- `Unreal::FUObjectDeleteListener::NotifyUObjectDeleted(const UObjectBase*, int32 Index)` and
  `OnUObjectArrayShutdown()`; registered with `UObjectArray::AddUObjectDeleteListener` /
  `RemoveUObjectDeleteListener` (`UEPseudo/include/Unreal/UObjectArray.hpp`).
- UE4SS's Lua layer (`LuaUObject.cpp`) keeps a mutex-guarded set of live wrappers keyed by object
  **address** (`HashObject()`), removes the entry in `NotifyUObjectDeleted`, and `IsValid` checks
  membership plus `IsUnreachable`. Address-keyed: a new object allocated at a freed address is
  indistinguishable from the old one for any wrapper created before the deletion was observed.
- `FUObjectItem::IsUnreachable()` is true when the Unreachable internal flag is set **or the item
  has no object**; `IsPendingKill()` is separate; `IsValid(bEvenIfPendingKill)` combines them.
  `UObject::IsUnreachable()` wraps `IndexToObject(GetInternalIndex())->IsUnreachable()`.
- `UObjectBase::GetInternalIndex()` gives the array index; `UObjectArray::IndexToObject(i)` gives the
  item.
- The engine's per-slot serial number exists only when someone asked for one: UEPseudo's
  `AllocateSerialNumber` has to call `UKismetSystemLibrary::Conv_ObjectToSoftObjectReference` (a
  real engine call) to make the engine allocate it. Pre-4.11 engines have no serial number at all.

## Decision
Do **not** use the engine serial number. Keep our own generation counter per array index.

```
Handle      { int32 index; uint32 generation; uint32 epoch; }
HandleTable   per-index generation[], global epoch, one mutex
acquire(i)    -> Handle{i, generation[i], epoch}      (grows the table if needed)
on_deleted(i) -> ++generation[i]                       (called from NotifyUObjectDeleted)
shutdown()    -> ++epoch                               (OnUObjectArrayShutdown)
is_current(h) -> h.epoch == epoch && h.generation == generation[h.index]
```

A Python `UObject` wrapper stores `{Handle, UObject* cached}`. Access goes through one function:

```
resolve(wrapper):
    if !table.is_current(handle)                       -> InvalidObjectError("destroyed")
    item = UObjectArray::IndexToObject(handle.index)
    if !item || item->GetUObject() != cached           -> InvalidObjectError("slot reused")
    if item->IsUnreachable()                           -> InvalidObjectError("unreachable")
    if item->IsPendingKill()                           -> InvalidObjectError("pending kill")
    return cached
```

Why this works:
- Slot reuse after deletion bumps the generation first, so old handles fail even if the new object
  has the same address (the weakness of the address-keyed Lua approach).
- The window between GC marking an object unreachable and the slot being freed is covered by
  `IsUnreachable`.
- No engine call is needed to create a handle, so creating wrappers is cheap and has no side effects.
- Works on every engine version that has the delete listener (4.25 included).

## Rules for callers
1. **The delete listener only touches the table.** It runs wherever the engine frees the object
   (normally the game thread, not guaranteed) and may hold engine locks. It must never take the GIL
   or call into Python, and the table mutex must never be held while calling into Unreal.
2. **Re-resolve after any engine call that can run GC or destroy objects** (spawning, loading,
   level travel, `ProcessEvent` of arbitrary functions). Do not cache the resolved pointer across
   such calls.
3. Only resolve on the game thread (see the threading epic); validation plus use is then atomic with
   respect to GC.
4. Borrowed views (struct/array/map views into an object's memory) hold the owning wrapper and
   resolve through it on every access, so they die with the owner.
5. Pending-kill objects are treated as invalid by default (differs from UE4SS Lua, which checks
   unreachable only). Revisit if scripts need to inspect dying objects.
6. Python equality/hash of a wrapper is `(index, generation, epoch)`, not the address.

## Open questions (verify in-game)
- That `NotifyUObjectDeleted` fires for every freed UObject in this 4.25 shipping build, and on
  which thread(s). Plan: log a counter plus thread id from a temporary listener.
- That objects cannot be freed on a non-game thread while a Python callback is running. If they can,
  rule 3 is not sufficient and `resolve` needs a lock or a GC-keep.
- Whether the table needs to be sized up front: `FUObjectArray::GetNumElements()` is available, but
  growth on demand avoids a large allocation (about 4 bytes per slot).

## Pointers read out of game memory (ticket 2bc.15)

A handle (`acquire`) trusts that the pointer it is given is a live `UObject`. Pointers the engine hands us through calls
(`GetClass`, hook contexts, `StaticFindObject`) are. Pointers we read out of properties, array elements and map entries are
not: the game may have left a raw pointer behind after the object died, or never initialised it.

`acquire_untrusted` is used for every such read. It rejects null, small and misaligned values; reads the object's index
inside a structured-exception guard (an unmapped address is not an exception for the script); and accepts the pointer only if
the slot named by that index holds exactly this address in the global object array. Freed-but-mapped memory has junk in the
index field, an interior pointer names the wrong slot, and both fail that last check. Anything rejected reads as `None`.
On Chivalry 2 the UnchainedPlugin seals a faulting read inside this guard too, because frame SEH does not
recover the Blueprint-VM fault class there — see `docs/FAULTS.md`.

What this does not catch: a pointer to freed memory that happens to hold the bytes of a live object's index (the slot match
stops it, since the slot's object must equal the address), and memory reused for a *new* object at the same address (that is a
live object; its handle is fresh). `ue4ss._selftest_poison_pointer` plants five kinds of bad pointer for the in-game test
(`python/examples/poison_main.py`).

## Findings from real games

Open questions above, answered with what was observed on UE 4.25 Shipping builds:

- **The delete listener fires for the objects we hold.** In the test game, every actor, view owner and the world that was
  destroyed (explicitly with `K2_DestroyActor` plus a forced GC, or by loading the map again with `OpenLevel`) made all
  wrappers and views of it report invalid and raise `InvalidObjectError`, and nothing crashed (`lifetime_main.py`: 34 PASS;
  `level_change_main.py`: 20 PASS, run three times). That also covers slot reuse: after 60 more actors were created the dead
  views stayed dead and equalled no new object.
- **Counts and threads.** Level change in the test game (3 populated actors, the world torn down and rebuilt): 94 deletions
  seen by the listener, 94 on the game thread, 0 on another thread, 0 before the game thread was known, identical in three
  runs. Chivalry 2, startup plus about 10 s of the menu: 4897 deletions, 4739 on the game thread, 0 elsewhere, 158 during
  startup before the game thread was known (thread unknown, not evidence of another thread).
- **Objects freed off the game thread.** None were seen in about 5000 deletions in either game. That is absence of
  evidence, not a guarantee: asynchronous loading in a busy game could free objects on a loading thread. Rule 3 stays
  (resolve only on the game thread); the listener already counts off-thread deletions (`_selftest_objects()`), so a run with a
  level streaming game will show it if it happens.
- **Forced GC.** `KismetSystemLibrary.CollectGarbage()` from Python on the game thread is safe with wrappers held; wrappers of
  collected objects fail the unreachable / pending-kill / slot checks as designed.

