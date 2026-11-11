#pragma once

// Python.h links python312_d.lib when _DEBUG is defined; only the release import library is shipped.
// Include Python.h through this header only, and never together with UE4SS headers.
#pragma push_macro("_DEBUG")
#undef _DEBUG
#include <Python.h>
#pragma pop_macro("_DEBUG")

#include <pyue/errors.hpp>

namespace pyue
{
    // Sets the Python exception matching `info`. Defined in UeModule.cpp.
    auto set_python_error(const ErrorInfo& info) -> void;

    // Runs `body` (returning PyObject*) and turns any C++ exception into a Python exception,
    // returning nullptr. Use at every boundary where Python calls into our C++ code, because a
    // C++ exception escaping into the interpreter terminates the game.
    template <typename Body>
    auto guard_object(Body&& body) -> PyObject*
    {
        try
        {
            return body();
        }
        catch (...)
        {
            set_python_error(classify_current_exception());
            return nullptr;
        }
    }

    // Same for int-returning slots (0 = success, -1 = error).
    template <typename Body>
    auto guard_int(Body&& body) -> int
    {
        try
        {
            return body();
        }
        catch (...)
        {
            set_python_error(classify_current_exception());
            return -1;
        }
    }
}
