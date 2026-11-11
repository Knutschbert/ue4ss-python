#include "PyArray.hpp"

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
        // A view of one TArray property. It remembers where the array lives (owner + property name), not an address: every
        // access looks the array up again, so it stays correct when the engine reallocates the array, and raises
        // InvalidObjectError once the owner was destroyed (docs/LIFETIME.md).
        struct ArrayObject
        {
            PyObject_HEAD
            properties::Target target;
            std::wstring name;
        };

        PyTypeObject* s_array_type = nullptr;

        auto self_of(PyObject* self) -> ArrayObject*
        {
            return reinterpret_cast<ArrayObject*>(self);
        }

        void array_dealloc(PyObject* self)
        {
            self_of(self)->target.~Target();
            self_of(self)->name.~basic_string();
            PyTypeObject* type = Py_TYPE(self);
            type->tp_free(self);
            Py_DECREF(type);
        }

        Py_ssize_t array_length(PyObject* self)
        {
            try
            {
                return static_cast<Py_ssize_t>(properties::array_info(self_of(self)->target, self_of(self)->name).length);
            }
            catch (...)
            {
                set_python_error(classify_current_exception());
                return -1;
            }
        }

        // One Python value as an element Value; throws with a Python exception set.
        auto element_value(PyObject* value, const properties::ArrayInfo& info) -> properties::Value
        {
            properties::Value converted;
            if (info.element.category == properties::Category::Struct)
            {
                converted.category = properties::Category::Struct;
                if (!struct_target_from_py(value, converted.structure))
                {
                    PyErr_Format(PyExc_TypeError, "a struct element needs a Struct, not %.200s", Py_TYPE(value)->tp_name);
                    throw PythonErrorSet{};
                }
                return converted;
            }
            if (info.element.category == properties::Category::Unsupported || info.element.category == properties::Category::Array)
            {
                throw UEError("array elements of type " + info.element.type_name + " are not supported yet");
            }
            return convert_value_from_python(value, info.element);
        }

        // Every item of any iterable as element Values (the iterable is fully read first, so `arr.extend(arr)` is fine).
        auto values_from_iterable(PyObject* iterable, const properties::ArrayInfo& info) -> std::vector<properties::Value>
        {
            PyObject* sequence = PySequence_Fast(iterable, "an array can only be assigned from an iterable");
            if (!sequence) throw PythonErrorSet{};
            std::vector<properties::Value> values;
            try
            {
                const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
                values.reserve(static_cast<std::size_t>(count));
                for (Py_ssize_t i = 0; i < count; ++i) values.push_back(element_value(PySequence_Fast_GET_ITEM(sequence, i), info));
            }
            catch (...)
            {
                Py_DECREF(sequence);
                throw;
            }
            Py_DECREF(sequence);
            return values;
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

        PyObject* element(ArrayObject* array, Py_ssize_t index)
        {
            return guard_object([&]() -> PyObject* {
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                const Py_ssize_t length = static_cast<Py_ssize_t>(info.length);
                if (index < 0) index += length;
                if (index < 0 || index >= length)
                {
                    PyErr_SetString(PyExc_IndexError, "array index out of range");
                    throw PythonErrorSet{};
                }
                return convert_value_to_python(properties::array_get(array->target, array->name, index));
            });
        }

        PyObject* array_item(PyObject* self, Py_ssize_t index)
        {
            // sq_item receives the index already adjusted for negative values only when sq_length is set; do it here too.
            return element(self_of(self), index);
        }

        int array_ass_item(PyObject* self, Py_ssize_t index, PyObject* value)
        {
            ArrayObject* array = self_of(self);
            return guard_int([&]() -> int {
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                const Py_ssize_t length = static_cast<Py_ssize_t>(info.length);
                if (!value) // del arr[i]
                {
                    index = adjust_index(index, length, "array deletion index out of range");
                    properties::array_replace(array->target, array->name, index, index + 1, {});
                    return 0;
                }
                index = adjust_index(index, length, "array assignment index out of range");
                properties::array_set(array->target, array->name, index, element_value(value, info));
                return 0;
            });
        }

        // The slice's bounds when it has step 1 (the only kind that can change the length); raises otherwise.
        auto simple_slice(PyObject* key, Py_ssize_t length, Py_ssize_t& start, Py_ssize_t& stop) -> void
        {
            Py_ssize_t step = 1;
            Py_ssize_t count = 0;
            if (PySlice_Unpack(key, &start, &stop, &step) < 0) throw PythonErrorSet{};
            count = PySlice_AdjustIndices(length, &start, &stop, step);
            if (step != 1)
            {
                PyErr_SetString(PyExc_ValueError, "only slices with step 1 can be assigned or deleted");
                throw PythonErrorSet{};
            }
            if (stop < start) stop = start;
            (void)count;
        }

        PyObject* array_append(PyObject* self, PyObject* value)
        {
            ArrayObject* array = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                properties::array_replace(array->target, array->name, static_cast<std::int64_t>(info.length), static_cast<std::int64_t>(info.length), {element_value(value, info)});
                Py_RETURN_NONE;
            });
        }

        PyObject* array_extend(PyObject* self, PyObject* iterable)
        {
            ArrayObject* array = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                properties::array_replace(array->target, array->name, static_cast<std::int64_t>(info.length), static_cast<std::int64_t>(info.length), values_from_iterable(iterable, info));
                Py_RETURN_NONE;
            });
        }

        PyObject* array_insert(PyObject* self, PyObject* args)
        {
            ArrayObject* array = self_of(self);
            return guard_object([&]() -> PyObject* {
                Py_ssize_t index = 0;
                PyObject* value = nullptr;
                if (!PyArg_ParseTuple(args, "nO:insert", &index, &value)) throw PythonErrorSet{};
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                const Py_ssize_t length = static_cast<Py_ssize_t>(info.length);
                if (index < 0) index = index + length < 0 ? 0 : index + length; // like list.insert
                if (index > length) index = length;
                properties::array_replace(array->target, array->name, index, index, {element_value(value, info)});
                Py_RETURN_NONE;
            });
        }

        PyObject* array_pop(PyObject* self, PyObject* args)
        {
            ArrayObject* array = self_of(self);
            return guard_object([&]() -> PyObject* {
                Py_ssize_t index = -1;
                if (!PyArg_ParseTuple(args, "|n:pop", &index)) throw PythonErrorSet{};
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                const Py_ssize_t length = static_cast<Py_ssize_t>(info.length);
                if (length == 0)
                {
                    PyErr_SetString(PyExc_IndexError, "pop from empty array");
                    throw PythonErrorSet{};
                }
                index = adjust_index(index, length, "pop index out of range");
                PyObject* result = convert_value_to_python(properties::array_get(array->target, array->name, index));
                if (!result) throw PythonErrorSet{};
                try
                {
                    properties::array_replace(array->target, array->name, index, index + 1, {});
                }
                catch (...)
                {
                    Py_DECREF(result);
                    throw;
                }
                return result;
            });
        }

        PyObject* array_clear(PyObject* self, PyObject*)
        {
            ArrayObject* array = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                properties::array_replace(array->target, array->name, 0, static_cast<std::int64_t>(info.length), {});
                Py_RETURN_NONE;
            });
        }

        PyObject* array_to_list_method(PyObject* self, PyObject*)
        {
            return array_to_list(self_of(self)->target, self_of(self)->name);
        }

        // arr[i] and arr[a:b:c]; a slice gives a list of copies.
        PyObject* array_subscript(PyObject* self, PyObject* key)
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
            PyObject* all = array_to_list(self_of(self)->target, self_of(self)->name);
            if (!all) return nullptr;
            PyObject* sliced = PyObject_GetItem(all, key);
            Py_DECREF(all);
            return sliced;
        }

        int array_ass_subscript(PyObject* self, PyObject* key, PyObject* value)
        {
            ArrayObject* array = self_of(self);
            if (PyIndex_Check(key))
            {
                const Py_ssize_t index = PyNumber_AsSsize_t(key, PyExc_IndexError);
                if (index == -1 && PyErr_Occurred()) return -1;
                return array_ass_item(self, index, value);
            }
            if (!PySlice_Check(key))
            {
                PyErr_Format(PyExc_TypeError, "array indices must be integers or slices, not %.200s", Py_TYPE(key)->tp_name);
                return -1;
            }
            return guard_int([&]() -> int {
                const properties::ArrayInfo info = properties::array_info(array->target, array->name);
                Py_ssize_t start = 0;
                Py_ssize_t stop = 0;
                simple_slice(key, static_cast<Py_ssize_t>(info.length), start, stop);
                std::vector<properties::Value> values;
                if (value) values = values_from_iterable(value, info); // del arr[a:b] when value is null
                properties::array_replace(array->target, array->name, start, stop, values);
                return 0;
            });
        }

        PyObject* array_is_valid(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                if (properties::is_valid(self_of(self)->target)) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        // repr() must never raise.
        PyObject* array_repr(PyObject* self)
        {
            std::wstring text;
            try
            {
                const auto info = properties::array_info(self_of(self)->target, self_of(self)->name);
                text = L"<Array '" + self_of(self)->name + L"' of " + std::to_wstring(info.length) + L" " + std::wstring(info.element.type_name.begin(), info.element.type_name.end()) + L">";
            }
            catch (const InvalidObjectError&)
            {
                text = L"<Array (owner destroyed)>";
            }
            catch (...)
            {
                text = L"<Array (not readable here)>";
            }
            return py_from_wide(text);
        }

        PyMethodDef s_methods[] = {
                {"append", array_append, METH_O, "append(value): add one element at the end."},
                {"extend", array_extend, METH_O, "extend(iterable): add every item at the end. All items are converted before the array changes."},
                {"insert", array_insert, METH_VARARGS, "insert(index, value): insert before index (like list.insert)."},
                {"pop", array_pop, METH_VARARGS, "pop(index=-1): remove and return an element (a struct comes back as an owned copy)."},
                {"clear", array_clear, METH_NOARGS, "clear(): remove every element."},
                {"to_list", array_to_list_method, METH_NOARGS, "to_list() -> list: a copy of every element (struct elements are owned copies)."},
                {"is_valid", array_is_valid, METH_NOARGS, "is_valid() -> bool: False once the owner object was destroyed. Never raises."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_slots[] = {
                {Py_tp_doc, const_cast<char*>("A TArray property of an object or struct. It behaves like a list: len, indexing, slicing, iteration, item and slice assignment, del, append, extend, insert, pop, clear. Every access looks the array up again, so it is always current.")},
                {Py_tp_dealloc, reinterpret_cast<void*>(array_dealloc)},
                {Py_tp_repr, reinterpret_cast<void*>(array_repr)},
                {Py_sq_length, reinterpret_cast<void*>(array_length)},
                {Py_sq_item, reinterpret_cast<void*>(array_item)},
                {Py_sq_ass_item, reinterpret_cast<void*>(array_ass_item)},
                {Py_mp_length, reinterpret_cast<void*>(array_length)},
                {Py_mp_subscript, reinterpret_cast<void*>(array_subscript)},
                {Py_mp_ass_subscript, reinterpret_cast<void*>(array_ass_subscript)},
                {Py_tp_methods, s_methods},
                {0, nullptr},
        };

        PyType_Spec s_spec = {
                "ue4ss.Array",
                static_cast<int>(sizeof(ArrayObject)),
                0,
                Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
                s_slots,
        };
    }

    auto init_array_type(PyObject* module) -> bool
    {
        PyObject* type = PyType_FromSpec(&s_spec);
        if (!type) return false;
        s_array_type = reinterpret_cast<PyTypeObject*>(type);
        return PyModule_AddObjectRef(module, "Array", type) == 0;
    }

    auto make_py_array(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        if (!s_array_type)
        {
            PyErr_SetString(PyExc_RuntimeError, "ue4ss.Array is not initialised");
            return nullptr;
        }
        PyObject* object = s_array_type->tp_alloc(s_array_type, 0);
        if (!object) return nullptr;
        new (&self_of(object)->target) properties::Target(target);
        new (&self_of(object)->name) std::wstring(name);
        return object;
    }

    auto array_to_list(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        return guard_object([&]() -> PyObject* {
            const properties::ArrayInfo info = properties::array_info(target, name);
            PyObject* list = PyList_New(static_cast<Py_ssize_t>(info.length));
            if (!list) throw PythonErrorSet{};
            for (std::size_t i = 0; i < info.length; ++i)
            {
                PyObject* item = nullptr;
                try
                {
                    item = convert_value_to_python(properties::array_get(target, name, static_cast<std::int64_t>(i)));
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

    auto array_assign(const properties::Target& target, const std::wstring& name, PyObject* iterable) -> void
    {
        const properties::ArrayInfo info = properties::array_info(target, name);
        std::vector<properties::Value> values = values_from_iterable(iterable, info); // fully converted before anything changes
        properties::array_replace(target, name, 0, static_cast<std::int64_t>(info.length), values);
    }
}
