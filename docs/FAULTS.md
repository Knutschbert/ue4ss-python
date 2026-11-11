# Faults: engine-call hardening and the plugin seal

How a hardware fault inside game code (an access violation, a divide by zero) is kept from
ending the process, and what the UnchainedPlugin's VEH adds on Chivalry 2. The script-facing
summary is the "Sharp edges" section of `docs/API.md`; threading facts are in
`docs/THREADING.md`.

## Fault classes

- **Native faults** (a fault reached without the Blueprint VM in the frame stack): frame-based
  `__try`/`__except` recovers them in both games. `SafeCall`'s `call_guarded` catches them and
  the read guards catch their own.
- **The Blueprint-VM class** (a fault reached through the Blueprint VM, for example
  `execGetTarget` -> `UFunction::Invoke` -> `ProcessEvent`): on Chivalry 2 frame-based SEH
  does **not** recover these even when the fault is inside our `__try` (`gy7.1`). Evidence: an
  eager marker appended inside the `__except` handler (commit `9dba120`, verified present in
  the packaged `main.dll`) did not appear for the VM class, while the identical handler fires
  for a native AV. The engine source has no `__try`/`__except` around the native dispatch
  (`UObject::ProcessEvent`, `FFrame::Step`), so the remaining causes are a shipped-binary SEH
  wrapper this source does not show, or an x64 unwind failure (an intermediate VM->native
  frame lacks `.pdata`). Not resolved; irrelevant to acting: frame SEH cannot be relied on
  for this class, and only the plugin VEH (front of the vectored chain) runs before any of it.

## SafeCall (call_guarded)

- `pyue::call_guarded` (`src/SafeCall.cpp`) runs one engine call (`ProcessEvent`) inside
  `__try`/`__except`. A recoverable hardware fault comes back as `FaultInfo` and the caller
  (`src/PropertyApi.cpp` `call_function`) raises a clean `UEError` naming the access and the
  function; the engine's frames are abandoned without cleanup, so the game may misbehave
  afterwards (best effort; see `SafeCall.hpp`).
- C++ exceptions are never intercepted; stack overflow, breakpoints and everything not in the
  recoverable list keep propagating.
- Counted by `_selftest_faults_survived` (the in-game tests assert the count).

## The plugin seal (Chivalry only)

- When the UnchainedPlugin is loaded, its VEH (front of the vectored chain, so it runs before
  every frame handler) can **seal a faulting READ at the instruction**: it decodes the faulting
  instruction with `iced-x86`, and only for a plain memory-read load (no write-only operand,
  no stack/string instruction, no control transfer) zeroes the destination GPR(s), advances
  `Rip` past the load and returns `EXCEPTION_CONTINUE_EXECUTION`. Code bytes are read only
  within the faulting page.
- The seal is **opt-in per region**: the mod opens the plugin read-guard for the duration of a
  guarded region and the VEH only seals while it is open on that thread. Regions:
  `call_guarded` (every guarded engine call) and `read_index_guarded`/`acquire_untrusted`
  (every untrusted object-pointer read, `gy7.5`).
- Everything else keeps the plugin's own handling: log, stack, publish, sleep 5 s,
  `EXCEPTION_CONTINUE_SEARCH` — the pre-existing path for non-opt-in faults.
- **A WRITE fault is never sealed**, by design and by decode (write-only operands are refused).

## The handshake

- Exports on the plugin: `pyue_seh_begin_read_guard`, `pyue_seh_end_read_guard`,
  `pyue_seh_take_sealed_read`. The mod resolves them once via `GetModuleHandleA` +
  `GetProcAddress`; when the plugin is absent (test game) all three are no-ops and the native
  frame-SEH recovery applies unchanged.
- Per-thread nesting counter: regions may be open inside each other (a hook callback during a
  guarded call doing property reads); a seal is attributed to the innermost open region, whose
  consume reports it.
- Each region consumes what its window sealed (`take_sealed_read` returns and resets the
  count), so every seal is reported once. `call_guarded`'s except path drains a pending count
  so it cannot be mis-attributed to the next guarded read on the same thread.

## Dispositions

- A sealed read surfaces exactly like a real fault: `call_guarded` fills `FaultInfo` (code
  `EXCEPTION_ACCESS_VIOLATION`, not a write) and sets `FaultInfo.sealed` (`gy7.3`); the
  `UEError` message names the disposition — `(the plugin's VEH sealed the faulting read)` —
  while a fault that reached frame SEH says nothing extra.
- The plugin's own log records every seal with `Rip`, address and length
  (`READ AV sealed (gy7.2)` lines).
- A fault that escapes everything leaves only the post-mortem (the plugin's crash path);
  nothing can log from a dead process.

## Symbolized traces

- The packager ships `main.pdb` next to `main.dll` (`gy7.4`), so the plugin's crash backtrace
  resolves our mod's frames with real function names (`SymFromAddr`; dbghelp searches the
  loaded module's directory by default). Without the PDB dbghelp falls back to the nearest
  export, which labeled consecutive mod frames with one unrelated name (`uninstall_mod`).
- The release zip excludes the PDB (diagnostic artifact, not for users).

## Kill switch

- `PYUE_SEH_READ_RECOVER=0` in the game's environment disables the seal (read by the plugin at
  install). With it off, the plugin keeps the old log-and-continue path for every fault.

## Not covered

- Hook callbacks and faults anywhere outside `obj.Func(...)` are not caught by `call_guarded`.
- Faults in engine code outside the opt-in regions are not sealed (they keep the plugin's
  pre-existing handling; on the test game frame SEH recovers them where a guard exists).
- The script-side guards (`chivalry2.bot`'s `has_blackboard` gate, and refusing bare
  character classes in `place.spawn`) remain the real protection: a seal means the faulting
  instruction was skipped, not that the game state is consistent afterwards.

## Verified

- Chivalry, plugin loaded, unattended: `fault_main` PASS=10 FAIL=0 (11 `READ AV sealed` lines
  at the poisoned address, disposition named in the messages, game alive and functional);
  `poison_main` PASS=9 FAIL=0 with 401 seals in that run — before `gy7.5` it died mid-run
  (A/B-proven pre-existing: with the kill switch off it died identically).
- Test game, no plugin: `fault_main`/`poison_main` pass via native frame SEH; the handshake is
  a no-op there.
- The motivating crash re-verified on the seal layer (`25r`, 2026-10-30, unattended menu
  world): `gettarget_seal_probe_main` spawns a bot through the game's own AI spawn
  (`chivalry2.bots`), its controller has no blackboard (the crash case), and a raw
  `controller.GetTarget()` — the helper gate bypassed on purpose — raises the clean
  `UEError` `access violation reading address 0x0 (the plugin's VEH sealed the faulting
  read)) while running 'GetTarget'`; PASS=6 FAIL=0, the game alive and functional after the
  sealed fault, the bot removed cleanly. With the kill switch
  (`PYUE_SEH_READ_RECOVER=0`, propagated `WSLENV=NAME/w`) the same call killed the game
  (PASS=3, "the game exited on its own", not a hang) and the plugin log holds the full
  symbolized stack of the original crash: `UBlackboardComponent::GetValueAsObject` <-
  `ATBLAIController::GetTarget` <- `execGetTarget` <- `UFunction::Invoke` <-
  `UObject::ProcessEvent` <- `pyue::call_guarded` <- `pyue::call_function_from_python` —
  our frames symbolized (the `gy7.4` PDB work), exactly the class frame SEH cannot recover
  (`gy7.1`).
