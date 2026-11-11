#pragma once

#include <cstdint>
#include <string>

#include "PythonApi.hpp"

// Times Python callbacks that run on the game thread and warns (to sys.stderr, so into the UE4SS log) when one takes longer than
// the threshold. The policy (threshold, per-label rate limit) is core/include/pyue/slow_callback.hpp.
namespace pyue::slow
{
    auto set_threshold_ms(std::int64_t milliseconds) -> void; // 0 switches the warnings off; default 50
    auto threshold_ms() -> std::int64_t;

    // Describes the callback for the message; called only when the callback was slow. May throw; the scope then uses the kind alone.
    using Describe = std::string (*)(const void* subject);

    // Put one on the stack around the call into Python. The caller holds the GIL for the whole scope (the warning is written
    // through sys.stderr when the scope ends).
    class Scope
    {
    public:
        explicit Scope(const char* kind, Describe describe = nullptr, const void* subject = nullptr);
        ~Scope();
        Scope(const Scope&) = delete;
        auto operator=(const Scope&) -> Scope& = delete;

    private:
        const char* m_kind;
        Describe m_describe;
        const void* m_subject;
        std::int64_t m_start_ns;
    };

    // ue4ss.set_slow_callback_warning(milliseconds)
    PyObject* py_set_slow_callback_warning(PyObject* self, PyObject* args);
}
