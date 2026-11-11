#pragma once

#include "PythonApi.hpp"

namespace pyue
{
    // PyInit function of the built-in `ue4ss` module (registered through the inittab).
    auto init_ue4ss_module() -> PyObject*;
}
