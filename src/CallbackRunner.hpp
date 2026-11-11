#pragma once

#include "PythonApi.hpp"

namespace pyue
{
    // Calls `callable()` with the GIL held by the caller. A Python exception is printed as a full
    // traceback to sys.stderr (the UE4SS log) and never propagates; sys.exit() inside a callback is
    // logged and ignored instead of ending the game. Returns true if the call succeeded.
    auto call_logged(PyObject* callable) -> bool;
}
