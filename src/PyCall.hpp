#pragma once

#include <vector>

#include "PythonApi.hpp"

#include <pyue/call_binding.hpp>

#include "ObjectApi.hpp"
#include "PropertyApi.hpp"

namespace pyue
{
    // Creates ue4ss.BoundFunction and adds it to `module`. False with a Python exception set on failure.
    auto init_call_types(PyObject* module) -> bool;

    // A callable for `function` on `object` (see objects::api::call_context for which object it really runs on).
    auto make_bound_function(const objects::ObjectRef& object, const objects::ObjectRef& function) -> PyObject*;

    // A parameter block for `function`, filled from Python call arguments by the rules in core/call_binding.hpp.
    struct ParamBlock
    {
        objects::properties::Target block{};
        std::vector<ParamSpec> specs{};
        std::vector<objects::api::ParamInfo> params{};
    };
    auto build_param_block(const objects::ObjectRef& function, PyObject* args, PyObject* kwargs) -> ParamBlock;

    // Calls `function` on `object` with the Python arguments and returns the result: None, one value, or a tuple of the
    // return value followed by the outputs. New reference; throws like the rest of the bindings.
    auto call_function_from_python(const objects::ObjectRef& object, const objects::ObjectRef& function, PyObject* args, PyObject* kwargs) -> PyObject*;
}
