#pragma once

// Runs engine code so that a hardware fault inside it (access violation, illegal instruction, integer divide by zero)
// comes back as a result instead of killing the process. Includes no Unreal and no Python headers.
#include <cstdint>
//
// This is best effort. A fault abandons the engine's frames without running their destructors (the build uses /EHsc), so
// locks, scope counters and half-built objects in those frames are not cleaned up; after a fault the game may misbehave
// later. It is still far better than an immediate crash for a script that made one bad call. C++ exceptions are never
// intercepted: they propagate normally.
namespace pyue
{
    struct FaultInfo
    {
        unsigned long code{0};     // NTSTATUS-style exception code, for example 0xC0000005
        const void* address{nullptr}; // faulting instruction
        const void* data_address{nullptr}; // for an access violation: the address that was touched
        bool is_write{false};      // for an access violation: it was a write
        bool sealed{false};        // the plugin's VEH sealed a faulting read instead of it reaching frame SEH
    };

    using GuardedFunction = void (*)(void* argument);

    // Calls fn(argument). True if it returned normally; false if a handled hardware fault happened (then `fault` is filled).
    auto call_guarded(GuardedFunction function, void* argument, FaultInfo& fault) -> bool;

    // Handshake with the UnchainedPlugin's VEH (Chivalry). While open on this
    // thread, the plugin seals a faulting READ at the instruction (its VEH runs before every frame handler,
    // the only handler that reaches the fault class frame SEH cannot recover there). No-ops when the plugin
    // is absent (test game keeps the native __except). Nesting-safe per thread: regions may be open inside
    // each other, and each region consumes what its window sealed, so a seal is never used twice.
    void begin_read_guard();
    void end_read_guard();
    auto take_sealed_read() -> std::uint32_t;

    // Human readable description such as "access violation reading 0x00007ff0dead0000 at 0x00007ff6...".
    auto describe_fault(const FaultInfo& fault) -> const char*;
}
