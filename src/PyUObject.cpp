#include "PyUObject.hpp"

#include <string>

#include <new>

#include <pyue/errors.hpp>
#include <pyue/scalar.hpp>

#include "ObjectApi.hpp"
#include "PropertyApi.hpp"
#include "PyFName.hpp"
#include "PyProperties.hpp"
#include "PyCall.hpp"
#include "PyKinds.hpp"
#include "PyText.hpp"
#include "PyUObjectInternal.hpp"

namespace pyue
{
    namespace properties = objects::properties;

    namespace
    {
        PyTypeObject* s_uobject_type = nullptr;

        auto is_uobject(PyObject* object) -> bool
        {
            return s_uobject_type && PyObject_TypeCheck(object, s_uobject_type);
        }

        auto ref_of(PyObject* object) -> const objects::ObjectRef&
        {
            return reinterpret_cast<UObjectObject*>(object)->ref;
        }

        // Extracts the ref of `self` for a method, or throws the matching Python error.
        auto self_ref(PyObject* self) -> const objects::ObjectRef&
        {
            return ref_of(self);
        }

        auto target_of(PyObject* self) -> const properties::Target&
        {
            return reinterpret_cast<UObjectObject*>(self)->target;
        }


        // The Target member has a destructor (it holds a shared_ptr, empty for plain objects).
        void uobject_dealloc(PyObject* self)
        {
            reinterpret_cast<UObjectObject*>(self)->target.~Target();
            PyTypeObject* type = Py_TYPE(self);
            type->tp_free(self);
            Py_DECREF(type);
        }

        // A name that is not an attribute or a property may be a function of the object's class (or, for a class object,
        // of the class itself): obj.Func(...) calls it.
        PyObject* function_fallback(PyObject* self, const std::wstring& name)
        {
            const objects::ObjectRef method = objects::api::find_method(ref_of(self), name);
            if (method.handle.index < 0) return nullptr;
            return make_bound_function(ref_of(self), method);
        }

        PyObject* uobject_getattro(PyObject* self, PyObject* name)
        {
            return property_getattr(self, name, target_of, "ue4ss.UObject", function_fallback);
        }

        // call_function(function, *args, **kwargs): like obj.Name(...) but for a name that clashes with a method or
        // property, or for a UFunction object you already hold.
        PyObject* uobject_call_function(PyObject* self, PyObject* args, PyObject* kwargs)
        {
            return guard_object([&]() -> PyObject* {
                if (PyTuple_GET_SIZE(args) < 1)
                {
                    PyErr_SetString(PyExc_TypeError, "call_function() needs the function (a name or a UFunction) as its first argument");
                    throw PythonErrorSet{};
                }
                PyObject* which = PyTuple_GET_ITEM(args, 0);
                objects::ObjectRef function;
                if (PyUnicode_Check(which))
                {
                    std::wstring name;
                    if (!wide_from_py(which, name)) throw PythonErrorSet{};
                    function = objects::api::find_method(ref_of(self), name);
                    if (function.handle.index < 0)
                    {
                        PyErr_Format(PyExc_AttributeError, "the object has no function named '%U'", which);
                        throw PythonErrorSet{};
                    }
                }
                else if (!object_ref_from_py(which, function) || objects::api::kind_of(function) != objects::api::ObjectKind::Function)
                {
                    PyErr_Format(PyExc_TypeError, "call_function() needs a function name or a UFunction, not %.200s", Py_TYPE(which)->tp_name);
                    throw PythonErrorSet{};
                }
                PyObject* rest = PyTuple_GetSlice(args, 1, PyTuple_GET_SIZE(args));
                if (!rest) throw PythonErrorSet{};
                PyObject* result = nullptr;
                try
                {
                    result = call_function_from_python(ref_of(self), function, rest, kwargs);
                }
                catch (...)
                {
                    Py_DECREF(rest);
                    throw;
                }
                Py_DECREF(rest);
                return result;
            });
        }

        int uobject_setattro(PyObject* self, PyObject* name, PyObject* value)
        {
            return property_setattr(self, name, value, target_of, "ue4ss.UObject");
        }

        PyObject* uobject_get_property_value(PyObject* self, PyObject* name)
        {
            return property_get_method(self, name, target_of);
        }

        PyObject* uobject_set_property_value(PyObject* self, PyObject* args)
        {
            return property_set_method(self, args, target_of);
        }

        PyObject* uobject_get_property_names(PyObject* self, PyObject*)
        {
            return property_names_method(self, target_of);
        }

        PyObject* uobject_get_full_name(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return py_from_wide(objects::api::full_name(self_ref(self))); });
        }

        PyObject* uobject_get_fname(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return make_py_fname(objects::api::fname(self_ref(self))); });
        }

        PyObject* uobject_get_class(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return make_py_object(objects::api::class_of(self_ref(self))); });
        }

        PyObject* uobject_get_outer(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return make_py_object(objects::api::outer_of(self_ref(self))); });
        }

        PyObject* uobject_get_address(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return PyLong_FromUnsignedLongLong(objects::api::address(self_ref(self))); });
        }

        // is_valid() never raises, including off the game thread.
        PyObject* uobject_is_valid(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                if (objects::is_valid(self_ref(self))) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        PyObject* uobject_is_a(PyObject* self, PyObject* argument)
        {
            return guard_object([&]() -> PyObject* {
                bool result;
                if (is_uobject(argument))
                {
                    result = objects::api::is_a(self_ref(self), ref_of(argument));
                }
                else if (PyUnicode_Check(argument))
                {
                    std::wstring path;
                    if (!wide_from_py(argument, path)) throw PythonErrorSet{};
                    result = objects::api::is_a_path(self_ref(self), path);
                }
                else
                {
                    PyErr_Format(PyExc_TypeError, "is_a() argument must be a UObject class or a class path str, not %.200s", Py_TYPE(argument)->tp_name);
                    throw PythonErrorSet{};
                }
                if (result) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        // repr() must never raise or crash: fall back to a description that needs no engine access.
        PyObject* uobject_repr(PyObject* self)
        {
            const objects::ObjectRef& ref = self_ref(self);
            std::wstring text;
            try
            {
                text = L"<UObject '" + objects::api::full_name(ref) + L"'>";
            }
            catch (const InvalidObjectError&)
            {
                text = L"<UObject (destroyed) index=" + std::to_wstring(ref.handle.index) + L">";
            }
            catch (...)
            {
                text = L"<UObject index=" + std::to_wstring(ref.handle.index) + L" (not readable here)>";
            }
            return py_from_wide(text);
        }

        // Two wrappers are equal when they refer to the same incarnation of the same slot; a wrapper
        // of a destroyed object is never equal to a wrapper of a new object in that slot.
        PyObject* uobject_richcompare(PyObject* self, PyObject* other, int op)
        {
            if ((op != Py_EQ && op != Py_NE) || !is_uobject(other)) Py_RETURN_NOTIMPLEMENTED;
            const bool equal = ref_of(self).handle == ref_of(other).handle;
            if (equal == (op == Py_EQ)) Py_RETURN_TRUE;
            Py_RETURN_FALSE;
        }

        Py_hash_t uobject_hash(PyObject* self)
        {
            auto hash = static_cast<Py_hash_t>(hash_handle(ref_of(self).handle));
            return hash == -1 ? -2 : hash;
        }

        PyMethodDef s_methods[] = {
                {"get_full_name", uobject_get_full_name, METH_NOARGS, "get_full_name() -> str: 'ClassName /Path/To.Object'."},
                {"get_fname", uobject_get_fname, METH_NOARGS, "get_fname() -> FName: the object's name."},
                {"get_class", uobject_get_class, METH_NOARGS, "get_class() -> UObject: the object's class."},
                {"get_outer", uobject_get_outer, METH_NOARGS, "get_outer() -> UObject | None: the object that contains this one."},
                {"get_address", uobject_get_address, METH_NOARGS, "get_address() -> int: the object's address (changes between runs)."},
                {"is_valid", uobject_is_valid, METH_NOARGS, "is_valid() -> bool: False once the object was destroyed. Never raises."},
                {"get_property_value", uobject_get_property_value, METH_O, "get_property_value(name): read a property; same as obj.name, usable when the name clashes with a method."},
                {"set_property_value", uobject_set_property_value, METH_VARARGS, "set_property_value(name, value): write a property; same as obj.name = value."},
                {"call_function", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(uobject_call_function)), METH_VARARGS | METH_KEYWORDS,
                 "call_function(function, *args, **kwargs): call a function of this object by name or as a UFunction. Same as obj.Name(...). Returns None, the value, or a tuple of the return value and outputs."},
                {"get_property_names", uobject_get_property_names, METH_NOARGS, "get_property_names() -> list[str]: every property of the object's class, supported or not."},
                {"is_a", uobject_is_a, METH_O, "is_a(cls) -> bool: cls is a UObject class or a class path such as '/Script/Engine.Actor'."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_slots[] = {
                {Py_tp_doc, const_cast<char*>("An Unreal object. Holds a checked handle, not a pointer: using it after the object was destroyed raises InvalidObjectError.")},
                {Py_tp_dealloc, reinterpret_cast<void*>(uobject_dealloc)},
                {Py_tp_repr, reinterpret_cast<void*>(uobject_repr)},
                {Py_tp_richcompare, reinterpret_cast<void*>(uobject_richcompare)},
                {Py_tp_hash, reinterpret_cast<void*>(uobject_hash)},
                {Py_tp_getattro, reinterpret_cast<void*>(uobject_getattro)},
                {Py_tp_setattro, reinterpret_cast<void*>(uobject_setattro)},
                {Py_tp_methods, s_methods},
                {0, nullptr},
        };

        // No tp_new, and instantiation is disallowed (without the flag the type would inherit object's tp_new
        // and Python could create empty instances): scripts get objects from find_* functions, never by constructing them.
        PyType_Spec s_spec = {
                "ue4ss.UObject",
                static_cast<int>(sizeof(UObjectObject)),
                0,
                Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
                s_slots,
        };
    }

    auto init_uobject_type(PyObject* module) -> bool
    {
        PyObject* type = PyType_FromSpec(&s_spec);
        if (!type) return false;
        s_uobject_type = reinterpret_cast<PyTypeObject*>(type); // keeps the creation reference for the process lifetime
        if (PyModule_AddObjectRef(module, "UObject", type) != 0) return false;
        return init_kind_types(module, s_uobject_type);
    }

    auto make_py_object(const objects::ObjectRef& ref) -> PyObject*
    {
        if (ref.handle.index < 0 || !ref.object) Py_RETURN_NONE;
        // A reference the engine just handed out to an object that is pending kill or being collected reads as None, the
        // way the engine treats such references. (Refs here are always freshly read, never stale handles.)
        if (!objects::is_valid(ref)) Py_RETURN_NONE;
        if (!s_uobject_type)
        {
            PyErr_SetString(PyExc_RuntimeError, "ue4ss.UObject is not initialised");
            return nullptr;
        }
        // The most specific wrapper type: a class becomes a UClass, a function a UFunction, and so on.
        PyTypeObject* type = type_for_kind(objects::api::kind_of(ref), s_uobject_type);
        PyObject* object = type->tp_alloc(type, 0);
        if (!object) return nullptr;
        auto* wrapper = reinterpret_cast<UObjectObject*>(object);
        wrapper->ref = ref;
        new (&wrapper->target) properties::Target{ref, objects::ObjectRef{}, 0, nullptr};
        return object;
    }

    auto object_ref_from_py(PyObject* object, objects::ObjectRef& out) -> bool
    {
        if (is_uobject(object))
        {
            out = ref_of(object);
            return true;
        }
        // An engine class type of ue4ss.engine (Actor, LevelActor_Chair_C, ...) stands for its engine class object: `GameplayStatics.BeginDeferredActorSpawnFromClass(pawn, Actor, ...)`
        // reads naturally and means `Actor.static_class()`. Anything else that is not a UObject is still refused.
        if (PyType_Check(object) && PyObject_HasAttrString(object, "static_class"))
        {
            PyObject* result = PyObject_CallMethod(object, "static_class", nullptr);
            if (!result)
            {
                PyErr_Clear();
                return false;
            }
            const bool ok = is_uobject(result);
            if (ok) out = ref_of(result);
            Py_DECREF(result);
            return ok;
        }
        return false;
    }
}
