#pragma once

#include <string>

#include "PythonApi.hpp"

#include "PropertyApi.hpp"

namespace pyue
{
    // Creates ue4ss.FixedArray and adds it to `module`. False with a Python exception set on failure.
    auto init_fixed_array_type(PyObject* module) -> bool;

    // New reference to a ue4ss.FixedArray for the fixed-size array property `name` of `target`.
    auto make_py_fixed_array(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // A list with a copy of every element (new reference); a struct element is a Struct view's dict-less copy like Array.
    auto fixed_array_to_list(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // obj.Name = iterable: the iterable must have exactly as many items as the array. Every item is converted first.
    auto fixed_array_assign(const objects::properties::Target& target, const std::wstring& name, PyObject* iterable) -> void;
}
