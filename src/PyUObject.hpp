#pragma once

#include "PythonApi.hpp"

#include "ObjectRegistry.hpp"

namespace pyue
{
    // Creates ue4ss.UObject and adds it to `module`. False with a Python exception set on failure.
    auto init_uobject_type(PyObject* module) -> bool;

    // New reference to a ue4ss.UObject for `ref`, or None for a null ref. nullptr with an exception set on error.
    auto make_py_object(const objects::ObjectRef& ref) -> PyObject*;

    // True and fills `out` if `object` is a ue4ss.UObject.
    auto object_ref_from_py(PyObject* object, objects::ObjectRef& out) -> bool;
}
