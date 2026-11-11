#pragma once

#include "PythonApi.hpp"

#include "ObjectApi.hpp"

namespace pyue
{
    // Creates ue4ss.UStruct, UClass, UScriptStruct, UFunction and UEnum as subtypes of `base` (ue4ss.UObject)
    // and adds them to `module`. False with a Python exception set on failure.
    auto init_kind_types(PyObject* module, PyTypeObject* base) -> bool;

    // The most specific wrapper type for an object of this kind (ue4ss.UObject itself for plain objects).
    auto type_for_kind(objects::api::ObjectKind kind, PyTypeObject* plain_object_type) -> PyTypeObject*;
}
