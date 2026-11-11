#include "PyFixedArray.hpp"

#include <new>
#include <vector>

#include <pyue/errors.hpp>

#include "PyProperties.hpp"
#include "PyStruct.hpp"
#include "PyText.hpp"

namespace properties = pyue::objects::properties;

namespace pyue
{
    namespace
    {
        // A view of one fixed-size array property (ArrayDim > 1). Like ue4ss.Array it remembers owner + name and looks the
        // property up on every access; unlike it the length cannot change.
        struct FixedArrayObject
        {
            PyObject_HEAD
            properties::Target target;
            std::wstring name;
        };

        PyTypeObject* s_type = nullptr;

        auto self_of(PyObject* self) -> FixedArrayObject*
        {
            return reinterpret_cast<FixedArrayObject*>(self);
        }

        void fixed_dealloc(PyObject* self)
        {
            self_of(self)->target.~Target();
            self_of(self)->name.~basic_string();
            PyTypeObject* type = Py_TYPE(self);
            type->tp_free(self);
            Py_DECREF(type);
        }

        Py_ssize_t fixed_length(PyObject* self)
        {
            try
            {
                return static_cast<Py_ssize_t>(properties::fixed_info(self_of(self)->target, self_of(self)->name).length);
            }
            catch (...)
            {
                set_python_error(classify_current_exception());
                return -1;
            }
        }

        auto element_value(PyObject* value, const properties::ArrayInfo& info) -> properties::Value
        {
            if (info.element.category == properties::Category::Struct)
            {
                properties::Value converted;
                converted.category = properties::Category::Struct;
                if (!struct_target_from_py(value, converted.structure))
                {
                    PyErr_Format(PyExc_TypeError, "a struct element needs a Struct, not %.200s", Py_TYPE(value)->tp_name);
                    throw PythonErrorSet{};
                }
                return converted;
            }
            return convert_value_from_python(value, info.element);
        }

        auto adjust_index(Py_ssize_t index, Py_ssize_t length, const char* message) -> Py_ssize_t
        {
            if (index < 0) index += length;
            if (index < 0 || index >= length)
            {
                PyErr_SetString(PyExc_IndexError, message);
                throw PythonErrorSet{};
            }
            return index;
        }

        PyObject* element(FixedArrayObject* array, Py_ssize_t index)
        {
            return guard_object([&]() -> PyObject* {
                const properties::ArrayInfo info = properties::fixed_info(array->target, array->name);
                index = adjust_index(index, static_cast<Py_ssize_t>(info.length), "array index out of range");
                return convert_value_to_python(properties::fixed_get(array->target, array->name, index));
            });
        }

        PyObject* fixed_item(PyObject* self, Py_ssize_t index)
        {
            return element(self_of(self), index);
        }

        int fixed_ass_item(PyObject* self, Py_ssize_t index, PyObject* value)
        {
            FixedArrayObject* array = self_of(self);
            return guard_int([&]() -> int {
                if (!value)
                {
                    PyErr_SetString(PyExc_TypeError, "a fixed-size array cannot shrink: its elements cannot be deleted");
                    throw PythonErrorSet{};
                }
                const properties::ArrayInfo info = properties::fixed_info(array->target, array->name);
                index = adjust_index(index, static_cast<Py_ssize_t>(info.length), "array assignment index out of range");
                properties::fixed_set(array->target, array->name, index, element_value(value, info));
                return 0;
            });
        }

        PyObject* fixed_to_list_method(PyObject* self, PyObject*)
        {
            return fixed_array_to_list(self_of(self)->target, self_of(self)->name);
        }

        // arr[i] and arr[a:b:c]; a slice gives a list of copies.
        PyObject* fixed_subscript(PyObject* self, PyObject* key)
        {
            if (PyIndex_Check(key))
            {
                const Py_ssize_t index = PyNumber_AsSsize_t(key, PyExc_IndexError);
                if (index == -1 && PyErr_Occurred()) return nullptr;
                return element(self_of(self), index);
            }
            if (!PySlice_Check(key))
            {
                PyErr_Format(PyExc_TypeError, "array indices must be integers or slices, not %.200s", Py_TYPE(key)->tp_name);
                return nullptr;
            }
            PyObject* all = fixed_array_to_list(self_of(self)->target, self_of(self)->name);
            if (!all) return nullptr;
            PyObject* sliced = PyObject_GetItem(all, key);
            Py_DECREF(all);
            return sliced;
        }

        // arr[i] = v only; a slice would have to keep the length, so write the elements one by one or assign the whole array.
        int fixed_ass_subscript(PyObject* self, PyObject* key, PyObject* value)
        {
            if (PyIndex_Check(key))
            {
                const Py_ssize_t index = PyNumber_AsSsize_t(key, PyExc_IndexError);
                if (index == -1 && PyErr_Occurred()) return -1;
                return fixed_ass_item(self, index, value);
            }
            if (PySlice_Check(key))
            {
                PyErr_SetString(PyExc_TypeError, "slices of a fixed-size array cannot be assigned: assign the elements one by one or the whole array");
                return -1;
            }
            PyErr_Format(PyExc_TypeError, "array indices must be integers, not %.200s", Py_TYPE(key)->tp_name);
            return -1;
        }

        PyObject* fixed_is_valid(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                if (properties::is_valid(self_of(self)->target)) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        // repr() must never raise.
        PyObject* fixed_repr(PyObject* self)
        {
            std::wstring text;
            try
            {
                const auto info = properties::fixed_info(self_of(self)->target, self_of(self)->name);
                text = L"<FixedArray '" + self_of(self)->name + L"' of " + std::to_wstring(info.length) + L" " + std::wstring(info.element.type_name.begin(), info.element.type_name.end()) + L">";
            }
            catch (const InvalidObjectError&)
            {
                text = L"<FixedArray (owner destroyed)>";
            }
            catch (...)
            {
                text = L"<FixedArray (not readable here)>";
            }
            return py_from_wide(text);
        }

        PyMethodDef s_methods[] = {
                {"to_list", fixed_to_list_method, METH_NOARGS, "to_list() -> list: a copy of every element."},
                {"is_valid", fixed_is_valid, METH_NOARGS, "is_valid() -> bool: False once the owner object was destroyed. Never raises."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_slots[] = {
                {Py_tp_doc, const_cast<char*>("A fixed-size array property (int Foo[4]) of an object or struct. It behaves like a list that cannot change length: len, indexing, slicing (read), iteration and item assignment. Assigning the property takes an iterable of exactly the same length.")},
                {Py_tp_dealloc, reinterpret_cast<void*>(fixed_dealloc)},
                {Py_tp_repr, reinterpret_cast<void*>(fixed_repr)},
                {Py_sq_length, reinterpret_cast<void*>(fixed_length)},
                {Py_sq_item, reinterpret_cast<void*>(fixed_item)},
                {Py_sq_ass_item, reinterpret_cast<void*>(fixed_ass_item)},
                {Py_mp_length, reinterpret_cast<void*>(fixed_length)},
                {Py_mp_subscript, reinterpret_cast<void*>(fixed_subscript)},
                {Py_mp_ass_subscript, reinterpret_cast<void*>(fixed_ass_subscript)},
                {Py_tp_methods, s_methods},
                {0, nullptr},
        };

        PyType_Spec s_spec = {
                "ue4ss.FixedArray",
                static_cast<int>(sizeof(FixedArrayObject)),
                0,
                Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
                s_slots,
        };
    }

    auto init_fixed_array_type(PyObject* module) -> bool
    {
        PyObject* type = PyType_FromSpec(&s_spec);
        if (!type) return false;
        s_type = reinterpret_cast<PyTypeObject*>(type);
        return PyModule_AddObjectRef(module, "FixedArray", type) == 0;
    }

    auto make_py_fixed_array(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        if (!s_type)
        {
            PyErr_SetString(PyExc_RuntimeError, "ue4ss.FixedArray is not initialised");
            return nullptr;
        }
        PyObject* object = s_type->tp_alloc(s_type, 0);
        if (!object) return nullptr;
        new (&self_of(object)->target) properties::Target(target);
        new (&self_of(object)->name) std::wstring(name);
        return object;
    }

    auto fixed_array_to_list(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        return guard_object([&]() -> PyObject* {
            const properties::ArrayInfo info = properties::fixed_info(target, name);
            PyObject* list = PyList_New(static_cast<Py_ssize_t>(info.length));
            if (!list) throw PythonErrorSet{};
            for (std::size_t i = 0; i < info.length; ++i)
            {
                PyObject* item = nullptr;
                try
                {
                    item = convert_value_to_python(properties::fixed_get(target, name, static_cast<std::int64_t>(i)));
                }
                catch (...)
                {
                    Py_DECREF(list);
                    throw;
                }
                if (!item)
                {
                    Py_DECREF(list);
                    throw PythonErrorSet{};
                }
                PyList_SET_ITEM(list, static_cast<Py_ssize_t>(i), item);
            }
            return list;
        });
    }

    auto fixed_array_assign(const properties::Target& target, const std::wstring& name, PyObject* iterable) -> void
    {
        const properties::ArrayInfo info = properties::fixed_info(target, name);
        PyObject* sequence = PySequence_Fast(iterable, "a fixed-size array can only be assigned from an iterable");
        if (!sequence) throw PythonErrorSet{};
        std::vector<properties::Value> values;
        try
        {
            const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
            if (static_cast<std::size_t>(count) != info.length)
            {
                PyErr_Format(PyExc_ValueError, "'%s' has exactly %zu elements, got %zd", std::string(name.begin(), name.end()).c_str(), info.length, count);
                throw PythonErrorSet{};
            }
            for (Py_ssize_t i = 0; i < count; ++i) values.push_back(element_value(PySequence_Fast_GET_ITEM(sequence, i), info));
        }
        catch (...)
        {
            Py_DECREF(sequence);
            throw;
        }
        Py_DECREF(sequence);
        for (std::size_t i = 0; i < values.size(); ++i) properties::fixed_set(target, name, static_cast<std::int64_t>(i), values[i]);
    }
}
