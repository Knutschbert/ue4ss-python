#pragma once

#include "PythonApi.hpp"

#include "PropertyApi.hpp"

namespace pyue
{
    // Creates ue4ss.Struct and adds it to `module`. False with a Python exception set on failure.
    auto init_struct_type(PyObject* module) -> bool;

    // New reference to a ue4ss.Struct view for `target` (which must be a struct target).
    auto make_py_struct(const objects::properties::Target& target) -> PyObject*;

    // True and fills `out` if `object` is a ue4ss.Struct.
    auto struct_target_from_py(PyObject* object, objects::properties::Target& out) -> bool;
}
