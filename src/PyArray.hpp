#pragma once

#include <string>

#include "PythonApi.hpp"

#include "PropertyApi.hpp"

namespace pyue
{
    // Creates ue4ss.Array and adds it to `module`. False with a Python exception set on failure.
    auto init_array_type(PyObject* module) -> bool;

    // New reference to a ue4ss.Array for the TArray property `name` of `target`.
    auto make_py_array(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // A list with a copy of every element (new reference). Raises like indexing does.
    auto array_to_list(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // obj.Name = iterable: replaces the whole array. Every item is converted first, so a bad item leaves the array unchanged.
    auto array_assign(const objects::properties::Target& target, const std::wstring& name, PyObject* iterable) -> void;
}
