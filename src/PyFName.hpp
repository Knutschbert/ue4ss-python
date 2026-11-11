#pragma once

#include "PythonApi.hpp"

#include <pyue/name_id.hpp>

namespace pyue
{
    // Creates ue4ss.FName and adds it to `module`. Returns false with a Python exception set on failure.
    auto init_fname_type(PyObject* module) -> bool;

    // New reference to a ue4ss.FName for `id`, or nullptr with an exception set.
    auto make_py_fname(NameId id) -> PyObject*;

    // True and fills `out` if `object` is a ue4ss.FName.
    auto fname_from_py(PyObject* object, NameId& out) -> bool;
}
