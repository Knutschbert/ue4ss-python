#pragma once

#include <string>

#include "PythonApi.hpp"

#include "PropertyApi.hpp"

namespace pyue
{
    // Creates ue4ss.Map and ue4ss.Set and adds them to `module`. False with a Python exception set on failure.
    auto init_map_set_types(PyObject* module) -> bool;

    // New references to views of the TMap / TSet property `name` of `target`.
    auto make_py_map(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;
    auto make_py_set(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // dict(...) / list(...) copies of the whole container (new reference). Struct values become dicts when `plain`.
    auto map_to_dict(const objects::properties::Target& target, const std::wstring& name, bool plain) -> PyObject*;
    auto set_to_list(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // obj.Name = mapping / iterable: replaces the whole container; everything is converted first.
    auto map_assign(const objects::properties::Target& target, const std::wstring& name, PyObject* mapping) -> void;
    auto set_assign(const objects::properties::Target& target, const std::wstring& name, PyObject* iterable) -> void;
}
