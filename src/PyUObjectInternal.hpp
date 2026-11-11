#pragma once

#include "PythonApi.hpp"

#include "ObjectRegistry.hpp"
#include "PropertyApi.hpp"

// Layout shared by ue4ss.UObject and its subtypes (UStruct, UClass, UScriptStruct, UFunction, UEnum).
// The subtypes add methods only, never fields, so every one of them is a UObjectObject.
namespace pyue
{
    struct UObjectObject
    {
        PyObject_HEAD
        objects::ObjectRef ref;
        objects::properties::Target target; // {owner = ref, no struct}: what the shared property layer reads from
    };

    inline auto object_ref_of(PyObject* object) -> const objects::ObjectRef&
    {
        return reinterpret_cast<UObjectObject*>(object)->ref;
    }
}
