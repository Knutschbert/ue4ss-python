#include "PyFName.hpp"

#include <optional>
#include <string>

#include "PyText.hpp"
#include "UnrealBridge.hpp"

namespace pyue
{
    namespace
    {
        struct FNameObject
        {
            PyObject_HEAD
            NameId id;
        };

        PyTypeObject* s_fname_type = nullptr;

        auto new_fname(PyTypeObject* type, NameId id) -> PyObject*
        {
            PyObject* object = type->tp_alloc(type, 0);
            if (!object) return nullptr;
            reinterpret_cast<FNameObject*>(object)->id = id;
            return object;
        }

        auto id_of(PyObject* object) -> NameId
        {
            return reinterpret_cast<FNameObject*>(object)->id;
        }

        auto is_fname(PyObject* object) -> bool
        {
            return s_fname_type && PyObject_TypeCheck(object, s_fname_type);
        }

        // FName(text) adds the name to the engine's name table if needed, like FName("x") in UE4SS Lua.
        PyObject* fname_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
        {
            return guard_object([&]() -> PyObject* {
                if (kwargs && PyDict_Size(kwargs) > 0)
                {
                    PyErr_SetString(PyExc_TypeError, "FName() takes no keyword arguments");
                    throw PythonErrorSet{};
                }
                PyObject* argument = nullptr;
                if (!PyArg_UnpackTuple(args, "FName", 1, 1, &argument)) throw PythonErrorSet{};

                if (is_fname(argument)) return new_fname(type, id_of(argument));

                if (!PyUnicode_Check(argument))
                {
                    PyErr_Format(PyExc_TypeError, "FName() argument must be str or FName, not %.200s", Py_TYPE(argument)->tp_name);
                    throw PythonErrorSet{};
                }
                std::wstring text;
                if (!wide_from_py(argument, text)) throw PythonErrorSet{};

                const auto id = bridge::name_from_string(text, true);
                if (!id) throw UEError("the engine did not create the name");
                return new_fname(type, *id);
            });
        }

        // FName.find(text): the existing name, or None. Never adds to the name table.
        PyObject* fname_find(PyObject* cls, PyObject* argument)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring text;
                if (!wide_from_py(argument, text)) throw PythonErrorSet{};
                const auto id = bridge::name_from_string(text, false);
                if (!id) Py_RETURN_NONE;
                return new_fname(reinterpret_cast<PyTypeObject*>(cls), *id);
            });
        }

        PyObject* fname_str(PyObject* self)
        {
            return guard_object([&]() -> PyObject* { return py_from_wide(bridge::name_to_string(id_of(self))); });
        }

        PyObject* fname_to_string(PyObject* self, PyObject*)
        {
            return fname_str(self);
        }

        PyObject* fname_repr(PyObject* self)
        {
            return guard_object([&]() -> PyObject* {
                PyObject* text = fname_str(self);
                if (!text) throw PythonErrorSet{};
                PyObject* repr = PyUnicode_FromFormat("FName(%R)", text);
                Py_DECREF(text);
                return repr;
            });
        }

        // == against another FName compares the (index, number) identity; against a str it looks the
        // text up as a name (case-insensitive, like the engine) without adding it. Note that this makes
        // FName equal to a str that hashes differently: do not mix them as dict keys.
        PyObject* fname_richcompare(PyObject* self, PyObject* other, int op)
        {
            if (op != Py_EQ && op != Py_NE) Py_RETURN_NOTIMPLEMENTED;
            return guard_object([&]() -> PyObject* {
                bool equal;
                if (is_fname(other))
                {
                    equal = id_of(self) == id_of(other);
                }
                else if (PyUnicode_Check(other))
                {
                    std::wstring text;
                    if (!wide_from_py(other, text)) throw PythonErrorSet{};
                    const auto id = bridge::name_from_string(text, false);
                    equal = id && *id == id_of(self);
                }
                else
                {
                    Py_RETURN_NOTIMPLEMENTED;
                }
                if (equal == (op == Py_EQ)) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        Py_hash_t fname_hash(PyObject* self)
        {
            auto hash = static_cast<Py_hash_t>(hash_name(id_of(self)));
            return hash == -1 ? -2 : hash; // -1 means "error" to the interpreter
        }

        PyObject* fname_get_comparison_index(PyObject* self, void*)
        {
            return PyLong_FromUnsignedLong(id_of(self).index);
        }

        PyObject* fname_get_number(PyObject* self, void*)
        {
            return PyLong_FromLong(id_of(self).number);
        }

        PyMethodDef s_methods[] = {
                {"to_string", fname_to_string, METH_NOARGS, "to_string() -> str: the name as text."},
                {"find", fname_find, METH_O | METH_CLASS, "FName.find(text) -> FName | None: an existing name, never adds one."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyGetSetDef s_getset[] = {
                {"comparison_index", fname_get_comparison_index, nullptr, "Index of the name in the engine's name table (process specific).", nullptr},
                {"number", fname_get_number, nullptr, "Numeric suffix: 0 for none, otherwise N + 1 for 'Name_N'.", nullptr},
                {nullptr, nullptr, nullptr, nullptr, nullptr},
        };

        PyType_Slot s_slots[] = {
                {Py_tp_doc, const_cast<char*>("An Unreal name. FName('Foo_3') adds the name if it does not exist; FName.find('Foo_3') does not.")},
                {Py_tp_new, reinterpret_cast<void*>(fname_new)},
                {Py_tp_str, reinterpret_cast<void*>(fname_str)},
                {Py_tp_repr, reinterpret_cast<void*>(fname_repr)},
                {Py_tp_richcompare, reinterpret_cast<void*>(fname_richcompare)},
                {Py_tp_hash, reinterpret_cast<void*>(fname_hash)},
                {Py_tp_methods, s_methods},
                {Py_tp_getset, s_getset},
                {0, nullptr},
        };

        PyType_Spec s_spec = {
                "ue4ss.FName",
                static_cast<int>(sizeof(FNameObject)),
                0,
                Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
                s_slots,
        };
    }

    auto init_fname_type(PyObject* module) -> bool
    {
        PyObject* type = PyType_FromSpec(&s_spec);
        if (!type) return false;
        s_fname_type = reinterpret_cast<PyTypeObject*>(type); // keeps the creation reference for the process lifetime
        return PyModule_AddObjectRef(module, "FName", type) == 0;
    }

    auto make_py_fname(NameId id) -> PyObject*
    {
        if (!s_fname_type)
        {
            PyErr_SetString(PyExc_RuntimeError, "ue4ss.FName is not initialised");
            return nullptr;
        }
        return new_fname(s_fname_type, id);
    }

    auto fname_from_py(PyObject* object, NameId& out) -> bool
    {
        if (!is_fname(object)) return false;
        out = id_of(object);
        return true;
    }
}
