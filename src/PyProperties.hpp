#pragma once

#include "PythonApi.hpp"

#include "PropertyApi.hpp"

// Attribute access shared by ue4ss.UObject and ue4ss.Struct: both expose properties of a Target.
namespace pyue
{
    using TargetOf = const objects::properties::Target& (*)(PyObject* self);

    // Called for a name that is neither an attribute nor a property: returns a new reference, or nullptr WITHOUT an
    // exception set if there is nothing by that name either.
    using AttributeFallback = PyObject* (*)(PyObject* self, const std::wstring& name);

    // tp_getattro: normal attributes first, then a property of the target, then the optional fallback (for example a
    // function of the object). Raises AttributeError if none of them exists.
    auto property_getattr(PyObject* self, PyObject* name, TargetOf target_of, const char* type_name, AttributeFallback fallback = nullptr) -> PyObject*;

    // Writes one property of the target from a Python value (the same strict conversions as attribute assignment).
    auto property_assign(const objects::properties::Target& target, const std::wstring& name, PyObject* value) -> void;

    // The value of the named property as a Python object (new reference), converted like an attribute read.
    auto property_value_as_python(const objects::properties::Target& target, const std::wstring& name) -> PyObject*;

    // One property value as a Python object (new reference; nullptr with an exception set), and a Python value as a Value of
    // the given type (strict conversions; throws like attribute assignment). Not for Struct or Array categories.
    auto convert_value_to_python(const objects::properties::Value& value) -> PyObject*;
    auto convert_value_from_python(PyObject* object, const objects::properties::PropertyInfo& info) -> objects::properties::Value;

    // Like convert_value_from_python for a container's key, value or element: a struct is taken from a ue4ss.Struct.
    auto convert_element_from_python(PyObject* object, const objects::properties::PropertyInfo& info) -> objects::properties::Value;

    // tp_setattro: methods are read-only; anything else is written to the property of that name.
    auto property_setattr(PyObject* self, PyObject* name, PyObject* value, TargetOf target_of, const char* type_name) -> int;

    // get_property_value(name), set_property_value(name, value), get_property_names()
    auto property_get_method(PyObject* self, PyObject* name, TargetOf target_of) -> PyObject*;
    auto property_set_method(PyObject* self, PyObject* args, TargetOf target_of) -> PyObject*;
    auto property_names_method(PyObject* self, TargetOf target_of) -> PyObject*;

    // Writes each (name, value) of `fields` (a dict) to the target's properties. Raises like a normal assignment.
    auto properties_assign_fields(const objects::properties::Target& target, PyObject* fields) -> void;

    // dict of every supported property (unsupported ones are left out); nested structs become dicts.
    auto properties_to_dict(const objects::properties::Target& target) -> PyObject*;
}
