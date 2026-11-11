#include "PyStruct.hpp"

#include <new>
#include <string>

#include <pyue/errors.hpp>

#include "PyProperties.hpp"
#include "PyText.hpp"
#include "UnrealBridge.hpp"

namespace pyue
{
    namespace
    {
        namespace properties = objects::properties;

        // A view of a struct stored inside an object (or inside another struct). It stores handles, never
        // pointers: the owner and the struct type are re-resolved on every access, so using the view after
        // the owner was destroyed raises InvalidObjectError (docs/LIFETIME.md rule 4).
        struct StructObject
        {
            PyObject_HEAD
            properties::Target target;
        };

        PyTypeObject* s_struct_type = nullptr;

        auto is_struct(PyObject* object) -> bool
        {
            return s_struct_type && PyObject_TypeCheck(object, s_struct_type);
        }

        auto target_of(PyObject* self) -> const properties::Target&
        {
            return reinterpret_cast<StructObject*>(self)->target;
        }

        // The Target holds a shared_ptr to an owned buffer (empty for views into an object).
        void struct_dealloc(PyObject* self)
        {
            reinterpret_cast<StructObject*>(self)->target.~Target();
            PyTypeObject* type = Py_TYPE(self);
            type->tp_free(self);
            Py_DECREF(type);
        }

        PyObject* struct_getattro(PyObject* self, PyObject* name)
        {
            return property_getattr(self, name, target_of, "ue4ss.Struct");
        }

        int struct_setattro(PyObject* self, PyObject* name, PyObject* value)
        {
            return property_setattr(self, name, value, target_of, "ue4ss.Struct");
        }

        PyObject* struct_get_property_value(PyObject* self, PyObject* name)
        {
            return property_get_method(self, name, target_of);
        }

        PyObject* struct_set_property_value(PyObject* self, PyObject* args)
        {
            return property_set_method(self, args, target_of);
        }

        PyObject* struct_get_property_names(PyObject* self, PyObject*)
        {
            return property_names_method(self, target_of);
        }

        PyObject* struct_to_dict(PyObject* self, PyObject*)
        {
            return properties_to_dict(target_of(self));
        }

        PyObject* struct_get_struct_name(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return py_from_wide(properties::struct_name(target_of(self))); });
        }

        PyObject* struct_copy(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return make_py_struct(properties::duplicate_struct(target_of(self))); });
        }

        PyObject* struct_is_owned(PyObject* self, PyObject*)
        {
            if (properties::owns_memory(target_of(self))) Py_RETURN_TRUE;
            Py_RETURN_FALSE;
        }

        // is_valid() never raises, including off the game thread.
        PyObject* struct_is_valid(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                if (properties::is_valid(target_of(self))) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        // repr() must never raise or crash.
        PyObject* struct_repr(PyObject* self)
        {
            const properties::Target& target = target_of(self);
            std::wstring text;
            try
            {
                text = L"<Struct '" + properties::struct_name(target) + (properties::owns_memory(target) ? L"' owned>" : properties::is_scoped(target) ? L"' hook parameters>" : L"' view>");
            }
            catch (const InvalidObjectError&)
            {
                text = properties::is_scoped(target) ? L"<Struct (hook parameters out of scope)>"
                       : target.is_owned()           ? L"<Struct (type destroyed)>"
                                                     : L"<Struct (owner destroyed)>";
            }
            catch (...)
            {
                text = L"<Struct (not readable here)>";
            }
            return py_from_wide(text);
        }

        // Two views are equal when they refer to the same place in the same incarnation of the same owner.
        // To compare contents use to_dict().
        PyObject* struct_richcompare(PyObject* self, PyObject* other, int op)
        {
            if ((op != Py_EQ && op != Py_NE) || !is_struct(other)) Py_RETURN_NOTIMPLEMENTED;
            const properties::Target& a = target_of(self);
            const properties::Target& b = target_of(other);
            const bool equal = a.owner.handle == b.owner.handle && a.structure.handle == b.structure.handle && a.offset == b.offset && a.owned == b.owned && a.base_override == b.base_override;
            if (equal == (op == Py_EQ)) Py_RETURN_TRUE;
            Py_RETURN_FALSE;
        }

        Py_hash_t struct_hash(PyObject* self)
        {
            const properties::Target& target = target_of(self);
            auto hash = static_cast<Py_hash_t>(hash_handle(target.owner.handle) ^ (hash_handle(target.structure.handle) * 31) ^ target.offset
                                               ^ reinterpret_cast<std::uintptr_t>(target.owned.get()) ^ reinterpret_cast<std::uintptr_t>(target.base_override));
            return hash == -1 ? -2 : hash;
        }

        PyMethodDef s_methods[] = {
                {"get_property_value", struct_get_property_value, METH_O, "get_property_value(name): read a field; same as view.name."},
                {"set_property_value", struct_set_property_value, METH_VARARGS, "set_property_value(name, value): write a field; same as view.name = value."},
                {"get_property_names", struct_get_property_names, METH_NOARGS, "get_property_names() -> list[str]: every field, supported or not."},
                {"to_dict", struct_to_dict, METH_NOARGS, "to_dict() -> dict: a copy of every supported field; nested structs become dicts and arrays become lists."},
                {"copy", struct_copy, METH_NOARGS, "copy() -> Struct: an independent copy that owns its memory and outlives the object this view came from."},
                {"is_owned", struct_is_owned, METH_NOARGS, "is_owned() -> bool: True for a struct you own (make_struct, copy), False for a view into an object."},
                {"get_struct_name", struct_get_struct_name, METH_NOARGS, "get_struct_name() -> str: the struct type's name."},
                {"is_valid", struct_is_valid, METH_NOARGS, "is_valid() -> bool: False once the owner object was destroyed. Never raises."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_slots[] = {
                {Py_tp_doc, const_cast<char*>("A struct value. A view into an object reads and writes the game's memory directly and stays valid only as long as its owner does; an owned struct (make_struct, copy) has its own memory.")},
                {Py_tp_dealloc, reinterpret_cast<void*>(struct_dealloc)},
                {Py_tp_getattro, reinterpret_cast<void*>(struct_getattro)},
                {Py_tp_setattro, reinterpret_cast<void*>(struct_setattro)},
                {Py_tp_repr, reinterpret_cast<void*>(struct_repr)},
                {Py_tp_richcompare, reinterpret_cast<void*>(struct_richcompare)},
                {Py_tp_hash, reinterpret_cast<void*>(struct_hash)},
                {Py_tp_methods, s_methods},
                {0, nullptr},
        };

        // No tp_new, and instantiation is disallowed (without the flag the type would inherit object's tp_new
        // and Python could create empty instances): views come from properties, never from construction.
        PyType_Spec s_spec = {
                "ue4ss.Struct",
                static_cast<int>(sizeof(StructObject)),
                0,
                Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
                s_slots,
        };
    }

    auto init_struct_type(PyObject* module) -> bool
    {
        PyObject* type = PyType_FromSpec(&s_spec);
        if (!type) return false;
        s_struct_type = reinterpret_cast<PyTypeObject*>(type); // keeps the creation reference for the process lifetime
        return PyModule_AddObjectRef(module, "Struct", type) == 0;
    }

    auto make_py_struct(const objects::properties::Target& target) -> PyObject*
    {
        if (!s_struct_type)
        {
            PyErr_SetString(PyExc_RuntimeError, "ue4ss.Struct is not initialised");
            return nullptr;
        }
        PyObject* object = s_struct_type->tp_alloc(s_struct_type, 0);
        if (!object) return nullptr;
        new (&reinterpret_cast<StructObject*>(object)->target) properties::Target(target);
        return object;
    }

    auto struct_target_from_py(PyObject* object, objects::properties::Target& out) -> bool
    {
        if (!is_struct(object)) return false;
        out = target_of(object);
        return true;
    }
}
