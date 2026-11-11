#include "PyMapSet.hpp"

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
        // Views of one TMap / TSet property. Like arrays they remember where the container lives (owner + property name)
        // and look it up again on every access, so they stay correct when the engine rehashes or reallocates it.
        struct ContainerObject
        {
            PyObject_HEAD
            properties::Target target;
            std::wstring name;
        };

        PyTypeObject* s_map_type = nullptr;
        PyTypeObject* s_set_type = nullptr;

        auto self_of(PyObject* self) -> ContainerObject*
        {
            return reinterpret_cast<ContainerObject*>(self);
        }

        void container_dealloc(PyObject* self)
        {
            self_of(self)->target.~Target();
            self_of(self)->name.~basic_string();
            PyTypeObject* type = Py_TYPE(self);
            type->tp_free(self);
            Py_DECREF(type);
        }

        auto make_container(PyTypeObject* type, const properties::Target& target, const std::wstring& name) -> PyObject*
        {
            if (!type)
            {
                PyErr_SetString(PyExc_RuntimeError, "ue4ss.Map / ue4ss.Set are not initialised");
                return nullptr;
            }
            PyObject* object = type->tp_alloc(type, 0);
            if (!object) return nullptr;
            new (&self_of(object)->target) properties::Target(target);
            new (&self_of(object)->name) std::wstring(name);
            return object;
        }

        // A Python value as a key / element / value Value of the container's type.
        auto converted(PyObject* object, const properties::PropertyInfo& info) -> properties::Value
        {
            return convert_element_from_python(object, info);
        }

        // ---------------------------------------------------------------- Map

        Py_ssize_t map_length(PyObject* self)
        {
            try
            {
                return static_cast<Py_ssize_t>(properties::map_info(self_of(self)->target, self_of(self)->name).length);
            }
            catch (...)
            {
                set_python_error(classify_current_exception());
                return -1;
            }
        }

        PyObject* key_error(PyObject* key)
        {
            PyErr_SetObject(PyExc_KeyError, key);
            return nullptr;
        }

        PyObject* map_subscript(PyObject* self, PyObject* key)
        {
            ContainerObject* map = self_of(self);
            bool missing = false;
            PyObject* result = guard_object([&]() -> PyObject* {
                const properties::MapInfo info = properties::map_info(map->target, map->name);
                const auto found = properties::map_find(map->target, map->name, converted(key, info.key));
                if (!found)
                {
                    missing = true;
                    return nullptr;
                }
                return convert_value_to_python(*found);
            });
            if (missing) return key_error(key);
            return result;
        }

        int map_ass_subscript(PyObject* self, PyObject* key, PyObject* value)
        {
            ContainerObject* map = self_of(self);
            return guard_int([&]() -> int {
                const properties::MapInfo info = properties::map_info(map->target, map->name);
                const properties::Value k = converted(key, info.key);
                if (!value) // del map[key]
                {
                    if (!properties::map_remove(map->target, map->name, k))
                    {
                        PyErr_SetObject(PyExc_KeyError, key);
                        throw PythonErrorSet{};
                    }
                    return 0;
                }
                properties::map_update(map->target, map->name, {properties::MapEntry{k, converted(value, info.value)}}, false);
                return 0;
            });
        }

        int map_contains(PyObject* self, PyObject* key)
        {
            ContainerObject* map = self_of(self);
            return guard_int([&]() -> int {
                const properties::MapInfo info = properties::map_info(map->target, map->name);
                return properties::map_find(map->target, map->name, converted(key, info.key)) ? 1 : 0;
            });
        }

        // Python values for the whole map: keys and values as lists in storage order.
        auto map_lists(ContainerObject* map, bool want_keys, bool want_values, bool plain) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                const std::vector<properties::MapEntry> entries = properties::map_entries(map->target, map->name);
                PyObject* list = PyList_New(static_cast<Py_ssize_t>(entries.size()));
                if (!list) throw PythonErrorSet{};
                for (std::size_t i = 0; i < entries.size(); ++i)
                {
                    PyObject* item = nullptr;
                    auto one = [&](const properties::Value& value) -> PyObject* {
                        if (plain && value.category == properties::Category::Struct) return properties_to_dict(value.structure);
                        return convert_value_to_python(value);
                    };
                    if (want_keys && want_values)
                    {
                        PyObject* k = one(entries[i].key);
                        PyObject* v = k ? one(entries[i].value) : nullptr;
                        item = (k && v) ? PyTuple_Pack(2, k, v) : nullptr;
                        Py_XDECREF(k);
                        Py_XDECREF(v);
                    }
                    else
                    {
                        item = one(want_keys ? entries[i].key : entries[i].value);
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

        PyObject* map_iter(PyObject* self)
        {
            PyObject* keys = map_lists(self_of(self), true, false, false);
            if (!keys) return nullptr;
            PyObject* iterator = PyObject_GetIter(keys);
            Py_DECREF(keys);
            return iterator;
        }

        PyObject* map_keys(PyObject* self, PyObject*) { return map_lists(self_of(self), true, false, false); }
        PyObject* map_values(PyObject* self, PyObject*) { return map_lists(self_of(self), false, true, false); }
        PyObject* map_items(PyObject* self, PyObject*) { return map_lists(self_of(self), true, true, false); }

        PyObject* map_get(PyObject* self, PyObject* args)
        {
            PyObject* key = nullptr;
            PyObject* fallback = Py_None;
            if (!PyArg_ParseTuple(args, "O|O:get", &key, &fallback)) return nullptr;
            PyObject* result = map_subscript(self, key);
            if (!result && PyErr_ExceptionMatches(PyExc_KeyError))
            {
                PyErr_Clear();
                return Py_NewRef(fallback);
            }
            return result;
        }

        PyObject* map_pop(PyObject* self, PyObject* args)
        {
            PyObject* key = nullptr;
            PyObject* fallback = nullptr;
            if (!PyArg_ParseTuple(args, "O|O:pop", &key, &fallback)) return nullptr;
            PyObject* result = map_subscript(self, key);
            if (!result)
            {
                if (fallback && PyErr_ExceptionMatches(PyExc_KeyError))
                {
                    PyErr_Clear();
                    return Py_NewRef(fallback);
                }
                return nullptr;
            }
            if (map_ass_subscript(self, key, nullptr) != 0)
            {
                Py_DECREF(result);
                return nullptr;
            }
            return result;
        }

        PyObject* map_clear(PyObject* self, PyObject*)
        {
            ContainerObject* map = self_of(self);
            return guard_object([&]() -> PyObject* {
                properties::map_update(map->target, map->name, {}, true);
                Py_RETURN_NONE;
            });
        }

        // Entries from a mapping (anything with .keys()) or an iterable of (key, value) pairs.
        auto entries_from_python(PyObject* source, const properties::MapInfo& info) -> std::vector<properties::MapEntry>
        {
            std::vector<properties::MapEntry> entries;
            PyObject* pairs = nullptr;
            if (PyObject_HasAttrString(source, "items"))
            {
                PyObject* items = PyObject_CallMethod(source, "items", nullptr);
                if (!items) throw PythonErrorSet{};
                pairs = PySequence_Fast(items, "items() must give pairs");
                Py_DECREF(items);
            }
            else
            {
                pairs = PySequence_Fast(source, "a map can only be assigned from a mapping or an iterable of (key, value) pairs");
            }
            if (!pairs) throw PythonErrorSet{};
            try
            {
                const Py_ssize_t count = PySequence_Fast_GET_SIZE(pairs);
                for (Py_ssize_t i = 0; i < count; ++i)
                {
                    PyObject* pair = PySequence_Fast(PySequence_Fast_GET_ITEM(pairs, i), "each entry must be a (key, value) pair");
                    if (!pair) throw PythonErrorSet{};
                    if (PySequence_Fast_GET_SIZE(pair) != 2)
                    {
                        Py_DECREF(pair);
                        PyErr_SetString(PyExc_ValueError, "each entry must be a (key, value) pair");
                        throw PythonErrorSet{};
                    }
                    try
                    {
                        entries.push_back(properties::MapEntry{converted(PySequence_Fast_GET_ITEM(pair, 0), info.key), converted(PySequence_Fast_GET_ITEM(pair, 1), info.value)});
                    }
                    catch (...)
                    {
                        Py_DECREF(pair);
                        throw;
                    }
                    Py_DECREF(pair);
                }
            }
            catch (...)
            {
                Py_DECREF(pairs);
                throw;
            }
            Py_DECREF(pairs);
            return entries;
        }

        PyObject* map_update_method(PyObject* self, PyObject* source)
        {
            ContainerObject* map = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::MapInfo info = properties::map_info(map->target, map->name);
                properties::map_update(map->target, map->name, entries_from_python(source, info), false);
                Py_RETURN_NONE;
            });
        }

        PyObject* map_to_dict_method(PyObject* self, PyObject*)
        {
            return map_to_dict(self_of(self)->target, self_of(self)->name, false);
        }

        PyObject* container_is_valid(PyObject* self, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                if (properties::is_valid(self_of(self)->target)) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        PyObject* map_repr(PyObject* self)
        {
            std::wstring text;
            try
            {
                const auto info = properties::map_info(self_of(self)->target, self_of(self)->name);
                text = L"<Map '" + self_of(self)->name + L"' of " + std::to_wstring(info.length) + L" " + std::wstring(info.key.type_name.begin(), info.key.type_name.end()) + L" -> " +
                       std::wstring(info.value.type_name.begin(), info.value.type_name.end()) + L">";
            }
            catch (const InvalidObjectError&)
            {
                text = L"<Map (owner destroyed)>";
            }
            catch (...)
            {
                text = L"<Map (not readable here)>";
            }
            return py_from_wide(text);
        }

        PyMethodDef s_map_methods[] = {
                {"keys", map_keys, METH_NOARGS, "keys() -> list: the keys, in the engine's storage order."},
                {"values", map_values, METH_NOARGS, "values() -> list: the values (struct values are owned copies)."},
                {"items", map_items, METH_NOARGS, "items() -> list of (key, value) tuples."},
                {"get", map_get, METH_VARARGS, "get(key, default=None)."},
                {"pop", map_pop, METH_VARARGS, "pop(key[, default]): remove the key and return its value."},
                {"clear", map_clear, METH_NOARGS, "clear(): remove every entry."},
                {"update", map_update_method, METH_O, "update(mapping_or_pairs): add or overwrite entries. Everything is converted before the map changes."},
                {"to_dict", map_to_dict_method, METH_NOARGS, "to_dict() -> dict: a copy (struct values stay owned Struct copies)."},
                {"is_valid", container_is_valid, METH_NOARGS, "is_valid() -> bool: False once the owner object was destroyed. Never raises."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_map_slots[] = {
                {Py_tp_doc, const_cast<char*>("A TMap property. It behaves like a dict: len, [], in, iteration over keys, keys/values/items/get/pop/update/clear, del. Keys may be numbers, bool, FName, str or objects (not structs). Every access looks the map up again.")},
                {Py_tp_dealloc, reinterpret_cast<void*>(container_dealloc)},
                {Py_tp_repr, reinterpret_cast<void*>(map_repr)},
                {Py_tp_iter, reinterpret_cast<void*>(map_iter)},
                {Py_mp_length, reinterpret_cast<void*>(map_length)},
                {Py_mp_subscript, reinterpret_cast<void*>(map_subscript)},
                {Py_mp_ass_subscript, reinterpret_cast<void*>(map_ass_subscript)},
                {Py_sq_contains, reinterpret_cast<void*>(map_contains)},
                {Py_tp_methods, s_map_methods},
                {0, nullptr},
        };

        PyType_Spec s_map_spec = {"ue4ss.Map", static_cast<int>(sizeof(ContainerObject)), 0, Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION, s_map_slots};

        // ---------------------------------------------------------------- Set

        Py_ssize_t set_length(PyObject* self)
        {
            try
            {
                return static_cast<Py_ssize_t>(properties::set_info(self_of(self)->target, self_of(self)->name).length);
            }
            catch (...)
            {
                set_python_error(classify_current_exception());
                return -1;
            }
        }

        int set_contains_slot(PyObject* self, PyObject* value)
        {
            ContainerObject* set = self_of(self);
            return guard_int([&]() -> int {
                const properties::SetInfo info = properties::set_info(set->target, set->name);
                return properties::set_contains(set->target, set->name, converted(value, info.element)) ? 1 : 0;
            });
        }

        PyObject* set_iter(PyObject* self)
        {
            PyObject* values = set_to_list(self_of(self)->target, self_of(self)->name);
            if (!values) return nullptr;
            PyObject* iterator = PyObject_GetIter(values);
            Py_DECREF(values);
            return iterator;
        }

        auto values_from_python(PyObject* iterable, const properties::SetInfo& info) -> std::vector<properties::Value>
        {
            PyObject* sequence = PySequence_Fast(iterable, "a set can only be assigned from an iterable");
            if (!sequence) throw PythonErrorSet{};
            std::vector<properties::Value> values;
            try
            {
                const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
                for (Py_ssize_t i = 0; i < count; ++i) values.push_back(converted(PySequence_Fast_GET_ITEM(sequence, i), info.element));
            }
            catch (...)
            {
                Py_DECREF(sequence);
                throw;
            }
            Py_DECREF(sequence);
            return values;
        }

        PyObject* set_add(PyObject* self, PyObject* value)
        {
            ContainerObject* set = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::SetInfo info = properties::set_info(set->target, set->name);
                properties::set_update(set->target, set->name, {converted(value, info.element)}, false);
                Py_RETURN_NONE;
            });
        }

        PyObject* set_update_method(PyObject* self, PyObject* iterable)
        {
            ContainerObject* set = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::SetInfo info = properties::set_info(set->target, set->name);
                properties::set_update(set->target, set->name, values_from_python(iterable, info), false);
                Py_RETURN_NONE;
            });
        }

        PyObject* set_discard(PyObject* self, PyObject* value)
        {
            ContainerObject* set = self_of(self);
            return guard_object([&]() -> PyObject* {
                const properties::SetInfo info = properties::set_info(set->target, set->name);
                properties::set_remove(set->target, set->name, converted(value, info.element));
                Py_RETURN_NONE;
            });
        }

        PyObject* set_remove_method(PyObject* self, PyObject* value)
        {
            ContainerObject* set = self_of(self);
            bool missing = false;
            PyObject* result = guard_object([&]() -> PyObject* {
                const properties::SetInfo info = properties::set_info(set->target, set->name);
                if (!properties::set_remove(set->target, set->name, converted(value, info.element)))
                {
                    missing = true;
                    return nullptr;
                }
                Py_RETURN_NONE;
            });
            if (missing) return key_error(value);
            return result;
        }

        PyObject* set_clear(PyObject* self, PyObject*)
        {
            ContainerObject* set = self_of(self);
            return guard_object([&]() -> PyObject* {
                properties::set_update(set->target, set->name, {}, true);
                Py_RETURN_NONE;
            });
        }

        PyObject* set_to_list_method(PyObject* self, PyObject*)
        {
            return set_to_list(self_of(self)->target, self_of(self)->name);
        }

        PyObject* set_to_set_method(PyObject* self, PyObject*)
        {
            PyObject* list = set_to_list(self_of(self)->target, self_of(self)->name);
            if (!list) return nullptr;
            PyObject* result = PySet_New(list);
            Py_DECREF(list);
            return result;
        }

        PyObject* set_repr(PyObject* self)
        {
            std::wstring text;
            try
            {
                const auto info = properties::set_info(self_of(self)->target, self_of(self)->name);
                text = L"<Set '" + self_of(self)->name + L"' of " + std::to_wstring(info.length) + L" " + std::wstring(info.element.type_name.begin(), info.element.type_name.end()) + L">";
            }
            catch (const InvalidObjectError&)
            {
                text = L"<Set (owner destroyed)>";
            }
            catch (...)
            {
                text = L"<Set (not readable here)>";
            }
            return py_from_wide(text);
        }

        PyMethodDef s_set_methods[] = {
                {"add", set_add, METH_O, "add(value)."},
                {"discard", set_discard, METH_O, "discard(value): remove it if present."},
                {"remove", set_remove_method, METH_O, "remove(value): remove it; KeyError if absent."},
                {"update", set_update_method, METH_O, "update(iterable): add every item. Everything is converted before the set changes."},
                {"clear", set_clear, METH_NOARGS, "clear(): remove every element."},
                {"to_list", set_to_list_method, METH_NOARGS, "to_list() -> list: the elements in the engine's storage order."},
                {"to_set", set_to_set_method, METH_NOARGS, "to_set() -> set: a Python set copy."},
                {"is_valid", container_is_valid, METH_NOARGS, "is_valid() -> bool: False once the owner object was destroyed. Never raises."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_set_slots[] = {
                {Py_tp_doc, const_cast<char*>("A TSet property. It behaves like a set: len, in, iteration, add/discard/remove/update/clear. Elements may be numbers, bool, FName, str or objects (not structs). Every access looks the set up again.")},
                {Py_tp_dealloc, reinterpret_cast<void*>(container_dealloc)},
                {Py_tp_repr, reinterpret_cast<void*>(set_repr)},
                {Py_tp_iter, reinterpret_cast<void*>(set_iter)},
                {Py_sq_length, reinterpret_cast<void*>(set_length)},
                {Py_sq_contains, reinterpret_cast<void*>(set_contains_slot)},
                {Py_tp_methods, s_set_methods},
                {0, nullptr},
        };

        PyType_Spec s_set_spec = {"ue4ss.Set", static_cast<int>(sizeof(ContainerObject)), 0, Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION, s_set_slots};
    }

    auto init_map_set_types(PyObject* module) -> bool
    {
        PyObject* map_type = PyType_FromSpec(&s_map_spec);
        if (!map_type) return false;
        s_map_type = reinterpret_cast<PyTypeObject*>(map_type);
        if (PyModule_AddObjectRef(module, "Map", map_type) != 0) return false;
        PyObject* set_type = PyType_FromSpec(&s_set_spec);
        if (!set_type) return false;
        s_set_type = reinterpret_cast<PyTypeObject*>(set_type);
        return PyModule_AddObjectRef(module, "Set", set_type) == 0;
    }

    auto make_py_map(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        return make_container(s_map_type, target, name);
    }

    auto make_py_set(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        return make_container(s_set_type, target, name);
    }

    auto map_to_dict(const properties::Target& target, const std::wstring& name, bool plain) -> PyObject*
    {
        PyObject* map = make_container(s_map_type, target, name);
        if (!map) return nullptr;
        PyObject* items = map_lists(self_of(map), true, true, plain);
        Py_DECREF(map);
        if (!items) return nullptr;
        PyObject* dict = PyDict_New();
        if (!dict)
        {
            Py_DECREF(items);
            return nullptr;
        }
        const Py_ssize_t count = PyList_GET_SIZE(items);
        for (Py_ssize_t i = 0; i < count; ++i)
        {
            PyObject* pair = PyList_GET_ITEM(items, i);
            if (PyDict_SetItem(dict, PyTuple_GET_ITEM(pair, 0), PyTuple_GET_ITEM(pair, 1)) != 0)
            {
                Py_DECREF(items);
                Py_DECREF(dict);
                return nullptr;
            }
        }
        Py_DECREF(items);
        return dict;
    }

    auto set_to_list(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        return guard_object([&]() -> PyObject* {
            const std::vector<properties::Value> values = properties::set_values(target, name);
            PyObject* list = PyList_New(static_cast<Py_ssize_t>(values.size()));
            if (!list) throw PythonErrorSet{};
            for (std::size_t i = 0; i < values.size(); ++i)
            {
                PyObject* item = convert_value_to_python(values[i]);
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

    auto map_assign(const properties::Target& target, const std::wstring& name, PyObject* mapping) -> void
    {
        const properties::MapInfo info = properties::map_info(target, name);
        properties::map_update(target, name, entries_from_python(mapping, info), true);
    }

    auto set_assign(const properties::Target& target, const std::wstring& name, PyObject* iterable) -> void
    {
        const properties::SetInfo info = properties::set_info(target, name);
        properties::set_update(target, name, values_from_python(iterable, info), true);
    }
}
