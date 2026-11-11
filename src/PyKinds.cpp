#include "PyKinds.hpp"

#include <string>

#include <pyue/call_binding.hpp>
#include <pyue/errors.hpp>

#include "ObjectApi.hpp"
#include "PyCall.hpp"
#include "PyText.hpp"
#include "PyStruct.hpp"
#include "PyUObjectInternal.hpp"
#include "PyUObject.hpp"

namespace pyue
{
    namespace
    {
        PyTypeObject* s_struct_type = nullptr;
        PyTypeObject* s_class_type = nullptr;
        PyTypeObject* s_script_struct_type = nullptr;
        PyTypeObject* s_function_type = nullptr;
        PyTypeObject* s_enum_type = nullptr;

        auto is_uobject_subtype(PyObject* object, PyTypeObject* type) -> bool
        {
            return type && PyObject_TypeCheck(object, type);
        }

        auto list_of_strings(const std::vector<std::wstring>& names) -> PyObject*
        {
            PyObject* list = PyList_New(0);
            if (!list) throw PythonErrorSet{};
            for (const std::wstring& name : names)
            {
                PyObject* item = py_from_wide(name);
                if (!item || PyList_Append(list, item) != 0)
                {
                    Py_XDECREF(item);
                    Py_DECREF(list);
                    throw PythonErrorSet{};
                }
                Py_DECREF(item);
            }
            return list;
        }

        auto set_item(PyObject* dict, const char* key, PyObject* value) -> void
        {
            if (!value || PyDict_SetItemString(dict, key, value) != 0)
            {
                Py_XDECREF(value);
                Py_DECREF(dict);
                throw PythonErrorSet{};
            }
            Py_DECREF(value);
        }

        // --- UStruct ---------------------------------------------------------------------------

        PyObject* struct_get_super(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return make_py_object(objects::api::super_of(object_ref_of(self))); });
        }

        // Overrides UObject.get_property_names: for a class or struct the useful answer is the fields its
        // instances have, not the properties of the metaclass.
        PyObject* struct_get_property_names(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return list_of_strings(objects::api::field_names(object_ref_of(self))); });
        }

        PyObject* struct_get_function_names(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return list_of_strings(objects::api::function_names(object_ref_of(self))); });
        }

        PyObject* struct_find_function(PyObject* self, PyObject* name)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring wide;
                if (!wide_from_py(name, wide)) throw PythonErrorSet{};
                return make_py_object(objects::api::find_function(object_ref_of(self), wide));
            });
        }

        PyObject* struct_get_properties(PyObject* self, PyObject* args, PyObject* kwargs);

        PyMethodDef s_struct_methods[] = {
                {"get_super", struct_get_super, METH_NOARGS, "get_super() -> UStruct | None: the parent class or struct."},
                {"get_property_names", struct_get_property_names, METH_NOARGS, "get_property_names() -> list[str]: the properties instances of this class or struct have, parents included."},
                {"get_properties", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(struct_get_properties)), METH_VARARGS | METH_KEYWORDS,
                 "get_properties(inherited=True) -> list[dict]: each property of the class or struct with its type: name, type (the engine property class), class, struct, enum (paths or None), array_dim, inner (descriptors of the element, or key and value), declared_in, flags. inherited=False lists only the properties this class declares."},
                {"get_function_names", struct_get_function_names, METH_NOARGS, "get_function_names() -> list[str]: the functions of this class, parents included."},
                {"find_function", struct_find_function, METH_O, "find_function(name) -> UFunction | None: a function of this class or a parent."},
                {nullptr, nullptr, 0, nullptr},
        };

        // --- UClass ----------------------------------------------------------------------------

        PyObject* class_get_cdo(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return make_py_object(objects::api::cdo_of(object_ref_of(self))); });
        }

        PyObject* class_is_child_of(PyObject* self, PyObject* argument)
        {
            return guard_object([&]() -> PyObject* {
                bool result;
                if (is_uobject_subtype(argument, s_class_type))
                {
                    result = objects::api::is_child_of(object_ref_of(self), object_ref_of(argument));
                }
                else if (PyUnicode_Check(argument))
                {
                    std::wstring path;
                    if (!wide_from_py(argument, path)) throw PythonErrorSet{};
                    result = objects::api::is_child_of_path(object_ref_of(self), path);
                }
                else
                {
                    PyErr_Format(PyExc_TypeError, "is_child_of() argument must be a UClass or a class path str, not %.200s", Py_TYPE(argument)->tp_name);
                    throw PythonErrorSet{};
                }
                if (result) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        PyMethodDef s_class_methods[] = {
                {"get_cdo", class_get_cdo, METH_NOARGS, "get_cdo() -> UObject | None: the class default object."},
                {"is_child_of", class_is_child_of, METH_O, "is_child_of(cls) -> bool: this class is cls or derives from it; cls is a UClass or a class path."},
                {nullptr, nullptr, 0, nullptr},
        };

        // --- UScriptStruct ---------------------------------------------------------------------

        PyMethodDef s_scriptstruct_methods[] = {
                {nullptr, nullptr, 0, nullptr},
        };

        // --- UFunction -------------------------------------------------------------------------

        PyObject* function_get_script(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                const std::vector<std::uint8_t> script = objects::api::function_script(object_ref_of(self));
                return PyBytes_FromStringAndSize(reinterpret_cast<const char*>(script.data()), static_cast<Py_ssize_t>(script.size()));
            });
        }

        PyObject* function_get_function_flags(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* { return PyLong_FromUnsignedLong(objects::api::function_flags(object_ref_of(self))); });
        }

        // A type descriptor as a dict: type, class, struct, enum (each a path str or None), array_dim, inner (list of descriptors).
        PyObject* type_to_dict(const objects::api::TypeInfo& type)
        {
            PyObject* dict = PyDict_New();
            if (!dict) throw PythonErrorSet{};
            const auto optional_path = [](const std::wstring& path) -> PyObject* { return path.empty() ? Py_NewRef(Py_None) : py_from_wide(path); };
            set_item(dict, "type", PyUnicode_FromString(type.type_name.c_str()));
            set_item(dict, "class", optional_path(type.class_path));
            set_item(dict, "struct", optional_path(type.struct_path));
            set_item(dict, "enum", optional_path(type.enum_path));
            set_item(dict, "array_dim", PyLong_FromLong(type.array_dim));
            PyObject* inner = PyList_New(0);
            if (!inner)
            {
                Py_DECREF(dict);
                throw PythonErrorSet{};
            }
            for (const objects::api::TypeInfo& item : type.inner)
            {
                PyObject* one = nullptr;
                try
                {
                    one = type_to_dict(item);
                }
                catch (...)
                {
                    Py_DECREF(inner);
                    Py_DECREF(dict);
                    throw;
                }
                if (PyList_Append(inner, one) != 0)
                {
                    Py_DECREF(one);
                    Py_DECREF(inner);
                    Py_DECREF(dict);
                    throw PythonErrorSet{};
                }
                Py_DECREF(one);
            }
            set_item(dict, "inner", inner);
            return dict;
        }

        PyObject* struct_get_properties(PyObject* self, PyObject* args, PyObject* kwargs)
        {
            return guard_object([&]() -> PyObject* {
                static const char* keywords[] = {"inherited", nullptr};
                int inherited = 1;
                if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p:get_properties", const_cast<char**>(keywords), &inherited)) throw PythonErrorSet{};
                PyObject* list = PyList_New(0);
                if (!list) throw PythonErrorSet{};
                for (const objects::api::FieldInfo& field : objects::api::field_infos(object_ref_of(self), inherited != 0))
                {
                    PyObject* dict = nullptr;
                    try
                    {
                        dict = type_to_dict(field.type);
                    }
                    catch (...)
                    {
                        Py_DECREF(list);
                        throw;
                    }
                    try
                    {
                        set_item(dict, "name", py_from_wide(field.name));
                        set_item(dict, "declared_in", py_from_wide(field.declared_in));
                        set_item(dict, "flags", PyLong_FromUnsignedLongLong(field.flags));
                    }
                    catch (...)
                    {
                        Py_DECREF(list); // set_item already released the dict
                        throw;
                    }
                    if (PyList_Append(list, dict) != 0)
                    {
                        Py_DECREF(dict);
                        Py_DECREF(list);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(dict);
                }
                return list;
            });
        }

        PyObject* function_get_params(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                PyObject* list = PyList_New(0);
                if (!list) throw PythonErrorSet{};
                for (const objects::api::ParamInfo& param : objects::api::function_params(object_ref_of(self)))
                {
                    PyObject* dict = PyDict_New();
                    if (!dict)
                    {
                        Py_DECREF(list);
                        throw PythonErrorSet{};
                    }
                    try
                    {
                        PyObject* described = type_to_dict(param.type);   // type, class, struct, enum, array_dim, inner
                        if (PyDict_Update(dict, described) != 0)
                        {
                            Py_DECREF(described);
                            throw PythonErrorSet{};
                        }
                        Py_DECREF(described);
                        set_item(dict, "name", py_from_wide(param.name));
                        set_item(dict, "is_out", PyBool_FromLong(param.is_out));
                        set_item(dict, "is_return", PyBool_FromLong(param.is_return));
                        set_item(dict, "is_const", PyBool_FromLong(param.is_const));
                        set_item(dict, "is_reference", PyBool_FromLong(param.is_reference));
                    }
                    catch (...)
                    {
                        Py_DECREF(list); // set_item already released the dict
                        throw;
                    }
                    if (PyList_Append(list, dict) != 0)
                    {
                        Py_DECREF(dict);
                        Py_DECREF(list);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(dict);
                }
                return list;
            });
        }

        // make_param_block(*args, **kwargs): the parameter block a call would use, filled from the arguments. Inputs are
        // matched by position or name; outputs and the return value start default-initialised. The result is an owned
        // Struct whose fields are the parameters.
        PyObject* function_make_param_block(PyObject* self, PyObject* args, PyObject* kwargs)
        {
            return guard_object([&]() -> PyObject* {
                const ParamBlock prepared = build_param_block(object_ref_of(self), args, kwargs);
                return make_py_struct(prepared.block);
            });
        }

        PyMethodDef s_function_methods[] = {
                {"make_param_block", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(function_make_param_block)), METH_VARARGS | METH_KEYWORDS,
                 "make_param_block(*args, **kwargs) -> Struct: the parameter block a call to this function would use, filled from the arguments (positional or by name, case-insensitive). Outputs and the return value start default-initialised."},
                {"get_function_flags", function_get_function_flags, METH_NOARGS, "get_function_flags() -> int: the engine's EFunctionFlags."},
                {"get_script", function_get_script, METH_NOARGS, "get_script() -> bytes: the compiled Blueprint bytecode of the function (empty for a native function); see ue4ss.bytecode.scan_calls to list what it calls."},
                {"get_params", function_get_params, METH_NOARGS, "get_params() -> list[dict]: for each parameter, in order: name, is_out, is_return, is_const, is_reference and the type descriptor (type, class, struct, enum, array_dim, inner) as in UStruct.get_properties()."},
                {nullptr, nullptr, 0, nullptr},
        };

        // --- UEnum -----------------------------------------------------------------------------

        PyObject* enum_get_names(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                PyObject* dict = PyDict_New();
                if (!dict) throw PythonErrorSet{};
                for (const auto& [name, value] : objects::api::enum_names(object_ref_of(self)))
                {
                    PyObject* key = py_from_wide(name);
                    PyObject* number = PyLong_FromLongLong(value);
                    if (!key || !number || PyDict_SetItem(dict, key, number) != 0)
                    {
                        Py_XDECREF(key);
                        Py_XDECREF(number);
                        Py_DECREF(dict);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(key);
                    Py_DECREF(number);
                }
                return dict;
            });
        }

        PyObject* enum_get_name_by_value(PyObject* self, PyObject* argument)
        {
            return guard_object([&]() -> PyObject* {
                if (!PyLong_Check(argument))
                {
                    PyErr_Format(PyExc_TypeError, "get_name_by_value() needs an int, not %.200s", Py_TYPE(argument)->tp_name);
                    throw PythonErrorSet{};
                }
                const long long value = PyLong_AsLongLong(argument);
                if (value == -1 && PyErr_Occurred()) throw PythonErrorSet{};
                const auto name = objects::api::enum_name_by_value(object_ref_of(self), value);
                if (!name) Py_RETURN_NONE;
                return py_from_wide(*name);
            });
        }

        PyObject* enum_get_value_by_name(PyObject* self, PyObject* argument)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring wanted;
                if (!wide_from_py(argument, wanted)) throw PythonErrorSet{};
                const auto position = wanted.rfind(L"::"); // accept "EFoo::Bar" as well as "Bar"
                if (position != std::wstring::npos) wanted = wanted.substr(position + 2);
                for (const auto& [name, value] : objects::api::enum_names(object_ref_of(self)))
                {
                    if (name == wanted) return PyLong_FromLongLong(value);
                }
                Py_RETURN_NONE;
            });
        }

        PyMethodDef s_enum_methods[] = {
                {"get_names", enum_get_names, METH_NOARGS, "get_names() -> dict[str, int]: every name and its value, without the 'EnumName::' prefix."},
                {"get_name_by_value", enum_get_name_by_value, METH_O, "get_name_by_value(value) -> str | None."},
                {"get_value_by_name", enum_get_value_by_name, METH_O, "get_value_by_name(name) -> int | None; the name may carry the 'EnumName::' prefix."},
                {nullptr, nullptr, 0, nullptr},
        };

        // --- type creation ---------------------------------------------------------------------

        // A subtype adds methods only: same layout as ue4ss.UObject, and no construction from Python.
        auto make_subtype(PyObject* module, const char* qualified_name, const char* short_name, const char* doc, PyMethodDef* methods, PyTypeObject* base)
                -> PyTypeObject*
        {
            PyType_Slot slots[] = {
                    {Py_tp_doc, const_cast<char*>(doc)},
                    {Py_tp_methods, methods},
                    {0, nullptr},
            };
            PyType_Spec spec = {
                    qualified_name,
                    0, // inherit the base layout
                    0,
                    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
                    slots,
            };
            PyObject* bases = PyTuple_Pack(1, reinterpret_cast<PyObject*>(base));
            if (!bases) return nullptr;
            PyObject* type = PyType_FromSpecWithBases(&spec, bases);
            Py_DECREF(bases);
            if (!type) return nullptr;
            if (PyModule_AddObjectRef(module, short_name, type) != 0)
            {
                Py_DECREF(type);
                return nullptr;
            }
            return reinterpret_cast<PyTypeObject*>(type); // keeps the creation reference for the process lifetime
        }
    }

    auto init_kind_types(PyObject* module, PyTypeObject* base) -> bool
    {
        s_struct_type = make_subtype(module, "ue4ss.UStruct", "UStruct", "A class, struct or function type.", s_struct_methods, base);
        if (!s_struct_type) return false;
        s_class_type = make_subtype(module, "ue4ss.UClass", "UClass", "An Unreal class.", s_class_methods, s_struct_type);
        if (!s_class_type) return false;
        s_script_struct_type = make_subtype(module, "ue4ss.UScriptStruct", "UScriptStruct", "The type of a struct.", s_scriptstruct_methods, s_struct_type);
        if (!s_script_struct_type) return false;
        s_function_type = make_subtype(module, "ue4ss.UFunction", "UFunction", "An Unreal function.", s_function_methods, s_struct_type);
        if (!s_function_type) return false;
        s_enum_type = make_subtype(module, "ue4ss.UEnum", "UEnum", "An Unreal enum.", s_enum_methods, base);
        return s_enum_type != nullptr;
    }

    auto type_for_kind(objects::api::ObjectKind kind, PyTypeObject* plain_object_type) -> PyTypeObject*
    {
        using Kind = objects::api::ObjectKind;
        switch (kind)
        {
        case Kind::Struct: return s_struct_type ? s_struct_type : plain_object_type;
        case Kind::Class: return s_class_type ? s_class_type : plain_object_type;
        case Kind::ScriptStruct: return s_script_struct_type ? s_script_struct_type : plain_object_type;
        case Kind::Function: return s_function_type ? s_function_type : plain_object_type;
        case Kind::Enum: return s_enum_type ? s_enum_type : plain_object_type;
        case Kind::Object: break;
        }
        return plain_object_type;
    }
}
