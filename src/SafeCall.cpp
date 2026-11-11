#include "SafeCall.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <cstdio>
#include <cstring>
#include <mutex>

namespace pyue
{
    namespace
    {
        constexpr unsigned long cpp_exception_code = 0xE06D7363; // MSVC C++ throw: not ours to intercept

        // Hardware faults a bad call can cause and the process can step past. Stack overflow, breakpoints, C++ exceptions and
        // everything else keep propagating (stack overflow leaves no stack to continue on).
        auto is_recoverable(unsigned long code) -> bool
        {
            switch (code)
            {
            case EXCEPTION_ACCESS_VIOLATION:
            case EXCEPTION_IN_PAGE_ERROR:
            case EXCEPTION_ILLEGAL_INSTRUCTION:
            case EXCEPTION_PRIV_INSTRUCTION:
            case EXCEPTION_INT_DIVIDE_BY_ZERO:
            case EXCEPTION_INT_OVERFLOW:
            case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
            case EXCEPTION_DATATYPE_MISALIGNMENT:
                return true;
            default:
                return false;
            }
        }

        int filter(EXCEPTION_POINTERS* pointers, FaultInfo* fault)
        {
            const unsigned long code = pointers->ExceptionRecord->ExceptionCode;
            if (code == cpp_exception_code || !is_recoverable(code)) return EXCEPTION_CONTINUE_SEARCH;
            fault->code = code;
            fault->address = pointers->ExceptionRecord->ExceptionAddress;
            if ((code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_IN_PAGE_ERROR) && pointers->ExceptionRecord->NumberParameters >= 2)
            {
                fault->is_write = pointers->ExceptionRecord->ExceptionInformation[0] == 1;
                fault->data_address = reinterpret_cast<const void*>(pointers->ExceptionRecord->ExceptionInformation[1]);
            }
            return EXCEPTION_EXECUTE_HANDLER;
        }

        thread_local char t_description[160];
    }

    // Diagnostic for gy7.1: an eager, crash-surviving marker so we can tell
    // whether call_guarded's __except handler actually fired for a fault. Ordinary logging to
    // UE4SS.log is buffered and lost if the process dies, so we append synchronously to a file
    // in the temp dir. Contributes to the gy7.3 handshake. Removed once the mechanism is proven.
    void diag_marker(const char* stage, unsigned long code)
    {
        char buffer[MAX_PATH];
        if (!GetTempPathA(sizeof(buffer), buffer)) return;
        const size_t n = strnlen(buffer, sizeof(buffer));
        std::snprintf(buffer + n, sizeof(buffer) - n, "pyue_seh_diag.log");
        FILE* f = std::fopen(buffer, "a");
        if (!f) return;
        std::fprintf(f, "gy7.1 stage=%s thread=%lu code=0x%08lX time=%ld\n", stage, GetCurrentThreadId(), code, static_cast<long>(GetTickCount64()));
        std::fclose(f);
    }

    // gy7.2 handshake: when the UnchainedPlugin is present in the process
    // (Chivalry), its VEH can recover a READ fault inside a call_guarded region on our
    // behalf (frame-based __except cannot for the Blueprint-VM class; see bots.md/gy7.1).
    // We open the handshake for the duration of the guarded engine call so the plugin seals
    // reads but nothing else. When the plugin is absent (test game) these are no-ops and the
    // existing native __except recovery still works.
    using ReadGuardFn = void (*)();
    using TakeSealedFn = std::uint32_t (*)();
    ReadGuardFn g_begin_read_guard = nullptr;
    ReadGuardFn g_end_read_guard = nullptr;
    TakeSealedFn g_take_sealed_read = nullptr;
    std::once_flag g_read_guard_once;

    void resolve_read_guards()
    {
        HMODULE plugin = GetModuleHandleA("UnchainedPlugin.dll");
        if (!plugin) return;
        g_begin_read_guard = reinterpret_cast<ReadGuardFn>(GetProcAddress(plugin, "pyue_seh_begin_read_guard"));
        g_end_read_guard = reinterpret_cast<ReadGuardFn>(GetProcAddress(plugin, "pyue_seh_end_read_guard"));
        g_take_sealed_read = reinterpret_cast<TakeSealedFn>(GetProcAddress(plugin, "pyue_seh_take_sealed_read"));
        if (!g_begin_read_guard || !g_end_read_guard || !g_take_sealed_read)
        {
            g_begin_read_guard = nullptr;
            g_end_read_guard = nullptr;
            g_take_sealed_read = nullptr;
        }
    }

    void begin_read_guard()
    {
        std::call_once(g_read_guard_once, resolve_read_guards);
        if (g_begin_read_guard) g_begin_read_guard();
    }

    void end_read_guard()
    {
        if (g_end_read_guard) g_end_read_guard();
    }

    // Number of READ faults the plugin's VEH sealed while our read-guard was open on this thread
    // (0 when the plugin is absent). Consumed so each seal is reported once.
    auto take_sealed_read() -> std::uint32_t
    {
        std::call_once(g_read_guard_once, resolve_read_guards);
        if (!g_take_sealed_read) return 0;
        return g_take_sealed_read();
    }

    auto call_guarded(GuardedFunction function, void* argument, FaultInfo& fault) -> bool
    {
        __try
        {
            // Open the plugin read-guard so a READ fault here is sealed by its VEH (ran before
            // any frame SEH; the only handler that reaches the Blueprint-VM class).
            begin_read_guard();
            function(argument);
            end_read_guard();
            // The plugin's VEH may have sealed a READ fault instead of letting it reach our
            // __except (the Blueprint-VM class never reaches here; see bots.md/gy7.1). Surface it
            // as a fault so the caller raises a clean UEError instead of using a garbage result.
            if (take_sealed_read() > 0)
            {
                fault.code = EXCEPTION_ACCESS_VIOLATION;
                fault.is_write = false;
                fault.data_address = nullptr;
                fault.address = nullptr;
                fault.sealed = true; // gy7.3: name the disposition (sealed, not frame SEH)
                return false;
            }
            return true;
        }
        __except (filter(GetExceptionInformation(), &fault))
        {
            // MSVC __except does not run destructors of locals, so we close the handshake
            // explicitly to keep the per-thread depth balanced. The plugin's end is clamped at
            // 0, so this stays safe even in the pathological case where begin never ran.
            // If this marker file appears after a fault that then killed the game, the handler
            // ran (so the exception DID reach our frame) and the fault must be later (H2). If it
            // is absent, the exception never reached our frame (H1).
            end_read_guard();
            // A READ sealed earlier in this same call (the engine caught it and carried on) would
            // otherwise stay pending and be mis-attributed to the next read_index_guarded on this
            // thread (gy7.5). The call is abandoned either way, so consume it.
            take_sealed_read();
            diag_marker("__except_handler", fault.code);
            return false;
        }
    }

    auto describe_fault(const FaultInfo& fault) -> const char*
    {
        switch (fault.code)
        {
        case EXCEPTION_ACCESS_VIOLATION:
        case EXCEPTION_IN_PAGE_ERROR:
            std::snprintf(t_description, sizeof(t_description), "%s %s address 0x%p at instruction 0x%p%s", fault.code == EXCEPTION_ACCESS_VIOLATION ? "access violation" : "in-page error",
                          fault.is_write ? "writing" : "reading", fault.data_address, fault.address,
                          fault.sealed ? " (the plugin's VEH sealed the faulting read)" : "");
            break;
        case EXCEPTION_INT_DIVIDE_BY_ZERO: std::snprintf(t_description, sizeof(t_description), "integer divide by zero at instruction 0x%p", fault.address); break;
        case EXCEPTION_ILLEGAL_INSTRUCTION: std::snprintf(t_description, sizeof(t_description), "illegal instruction at 0x%p", fault.address); break;
        default: std::snprintf(t_description, sizeof(t_description), "hardware exception 0x%08lX at instruction 0x%p", fault.code, fault.address); break;
        }
        return t_description;
    }
}
