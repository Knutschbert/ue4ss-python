#include "PyProperties.hpp"

#include "PyArray.hpp"
#include "PyFixedArray.hpp"
#include "PyMapSet.hpp"
#include "PyRotation.hpp"

#include <map>
#include <string>

#include <pyue/errors.hpp>
#include <pyue/scalar.hpp>

#include "ObjectApi.hpp"
#include "PyFName.hpp"
#include "PyStruct.hpp"
#include "PyText.hpp"
#include "PyUObject.hpp"
#include "UnrealBridge.hpp"

namespace pyue
{
    namespace properties = objects::properties;

    namespace
    {
        auto kind_name(ScalarKind kind) -> const char*
        {
            switch (kind)
            {
            case ScalarKind::Bool: return "bool";
            case ScalarKind::Int8: return "int8";
            case ScalarKind::Int16: return "int16";
            case ScalarKind::Int32: return "int32";
            case ScalarKind::Int64: return "int64";
            case ScalarKind::UInt8: return "uint8";
            case ScalarKind::UInt16: return "uint16";
            case ScalarKind::UInt32: return "uint32";
            case ScalarKind::UInt64: return "uint64";
            case ScalarKind::Float: return "float";
            case ScalarKind::Double: return "double";
            }
            return "?";
        }

        auto article_for(const char* word) -> const char*
        {
            const char first = word[0];
            return (first == 'a' || first == 'e' || first == 'i' || first == 'o' || first == 'u') ? "an" : "a";
        }

        [[noreturn]] void raise(PyObject* exception_type, const std::string& message)
        {
            PyErr_SetString(exception_type, message.c_str());
            throw PythonErrorSet{};
        }

        auto narrow(const std::wstring& text) -> std::string
        {
            return std::string(text.begin(), text.end()); // names in messages; non-ASCII becomes lossy, which is fine
        }

        // One IntEnum per engine enum, built on first use from the UEnum's names and cached for the process. nullptr means the
        // enum could not be turned into a Python class (reads then give plain ints).
        std::map<std::wstring, PyObject*> s_enum_classes;

        auto enum_class_for(const std::wstring& path) -> PyObject*
        {
            const auto cached = s_enum_classes.find(path);
            if (cached != s_enum_classes.end()) return cached->second;

            PyObject* made = nullptr;
            try
            {
                const objects::ObjectRef enumeration = objects::api::find_by_path(path);
                if (enumeration.handle.index >= 0 && objects::api::kind_of(enumeration) == objects::api::ObjectKind::Enum)
                {
                    PyObject* pairs = PyList_New(0);
                    for (const auto& [name, number] : objects::api::enum_names(enumeration))
                    {
                        if (name.empty() || name[0] == L'_') continue; // Enum reserves names like _MAX_
                        PyObject* key = py_from_wide(name);
                        PyObject* item = key ? Py_BuildValue("(OL)", key, static_cast<long long>(number)) : nullptr;
                        Py_XDECREF(key);
                        if (!item || PyList_Append(pairs, item) != 0) PyErr_Clear();
                        Py_XDECREF(item);
                    }
                    PyObject* enum_module = PyImport_ImportModule("enum");
                    PyObject* int_enum = enum_module ? PyObject_GetAttrString(enum_module, "IntEnum") : nullptr;
                    const auto dot = path.rfind(L'.');
                    PyObject* class_name = py_from_wide(dot == std::wstring::npos ? path : path.substr(dot + 1));
                    if (int_enum && class_name) made = PyObject_CallFunctionObjArgs(int_enum, class_name, pairs, nullptr);
                    if (!made) PyErr_Clear();
                    Py_XDECREF(class_name);
                    Py_XDECREF(int_enum);
                    Py_XDECREF(enum_module);
                    Py_DECREF(pairs);
                }
            }
            catch (...)
            {
                PyErr_Clear();
            }
            s_enum_classes.emplace(path, made); // the reference lives for the process
            return made;
        }

        // The IntEnum member for an enum property's value; a number the enum does not name stays a plain int.
        auto enum_member(const std::wstring& path, PyObject* number) -> PyObject*
        {
            PyObject* cls = enum_class_for(path);
            if (!cls) return number;
            PyObject* member = PyObject_CallFunctionObjArgs(cls, number, nullptr);
            if (!member)
            {
                PyErr_Clear(); // ValueError: not a named value
                return number;
            }
            Py_DECREF(number);
            return member;
        }

        auto value_to_python(const properties::Value& value) -> PyObject*
        {
            switch (value.category)
            {
            case properties::Category::Scalar:
                switch (value.scalar.type)
                {
                case ScalarValue::Type::Bool: return PyBool_FromLong(value.scalar.boolean);
                case ScalarValue::Type::Signed: {
                    PyObject* number = PyLong_FromLongLong(value.scalar.signed_value);
                    return number && !value.enum_path.empty() ? enum_member(value.enum_path, number) : number;
                }
                case ScalarValue::Type::Unsigned: {
                    PyObject* number = PyLong_FromUnsignedLongLong(value.scalar.unsigned_value);
                    return number && !value.enum_path.empty() ? enum_member(value.enum_path, number) : number;
                }
                case ScalarValue::Type::Float: return PyFloat_FromDouble(value.scalar.float_value);
                }
                break;
            case properties::Category::Name: return make_py_fname(value.name);
            case properties::Category::String: return py_from_wide(value.text);
            case properties::Category::Object: return make_py_object(value.object); // a null ref becomes None
            case properties::Category::Struct: return make_py_struct(value.structure);
            case properties::Category::Text:
            case properties::Category::SoftPath: return py_from_wide(value.text);
            case properties::Category::Delegate:
            case properties::Category::MulticastDelegate: {
                // (object, FName) per binding: None / a tuple for a single-cast delegate, a list of tuples for a multicast one.
                PyObject* list = PyList_New(0);
                if (!list) return nullptr;
                for (const properties::Binding& binding : value.bindings)
                {
                    PyObject* object = make_py_object(binding.object);
                    PyObject* function = object ? make_py_fname(binding.function) : nullptr;
                    PyObject* pair = function ? PyTuple_Pack(2, object, function) : nullptr;
                    Py_XDECREF(object);
                    Py_XDECREF(function);
                    if (!pair || PyList_Append(list, pair) != 0)
                    {
                        Py_XDECREF(pair);
                        Py_DECREF(list);
                        return nullptr;
                    }
                    Py_DECREF(pair);
                }
                if (value.category == properties::Category::MulticastDelegate) return list;
                PyObject* single = PyList_GET_SIZE(list) ? Py_NewRef(PyList_GET_ITEM(list, 0)) : Py_NewRef(Py_None);
                Py_DECREF(list);
                return single;
            }
            case properties::Category::Array:
            case properties::Category::FixedArray:
            case properties::Category::Map:
            case properties::Category::Set:
            case properties::Category::Unsupported: break;
            }
            PyErr_SetString(PyExc_SystemError, "unsupported property value");
            return nullptr;
        }

        // Strict conversion: an out-of-range integer is an OverflowError, a float for an integer
        // property is a TypeError (no silent truncation), a bool accepts only True/False/0/1.
        auto scalar_from_python(PyObject* object, ScalarKind kind) -> ScalarValue
        {
            const std::string type = kind_name(kind);
            const std::string phrase = std::string(article_for(type.c_str())) + " " + type + " property";

            if (kind == ScalarKind::Bool)
            {
                if (PyBool_Check(object)) return ScalarValue::from_bool(object == Py_True);
                if (PyLong_Check(object))
                {
                    const long number = PyLong_AsLong(object);
                    if (number == -1 && PyErr_Occurred()) throw PythonErrorSet{};
                    if (number == 0 || number == 1) return ScalarValue::from_bool(number == 1);
                    raise(PyExc_ValueError, "a bool property accepts True, False, 0 or 1 (got another integer)");
                }
                raise(PyExc_TypeError, std::string("a bool property needs a bool, not ") + Py_TYPE(object)->tp_name);
            }

            if (is_float(kind))
            {
                if (PyUnicode_Check(object) || PyBytes_Check(object))
                {
                    raise(PyExc_TypeError, phrase + " needs a number, not " + Py_TYPE(object)->tp_name);
                }
                const double number = PyFloat_AsDouble(object); // ints too; huge ints raise OverflowError
                if (number == -1.0 && PyErr_Occurred()) throw PythonErrorSet{};
                if (!float_fits(kind, number)) raise(PyExc_OverflowError, "the number does not fit " + phrase);
                return ScalarValue::from_float(number);
            }

            if (!PyLong_Check(object)) raise(PyExc_TypeError, phrase + " needs an int, not " + Py_TYPE(object)->tp_name);

            if (is_unsigned_int(kind))
            {
                const unsigned long long number = PyLong_AsUnsignedLongLong(object); // negative or too big -> OverflowError
                if (number == static_cast<unsigned long long>(-1) && PyErr_Occurred()) throw PythonErrorSet{};
                if (!unsigned_fits(kind, number)) raise(PyExc_OverflowError, "the integer does not fit " + phrase);
                return ScalarValue::from_unsigned(number);
            }

            int overflow = 0;
            const long long number = PyLong_AsLongLongAndOverflow(object, &overflow);
            if (number == -1 && PyErr_Occurred()) throw PythonErrorSet{};
            if (overflow != 0 || !signed_fits(kind, number)) raise(PyExc_OverflowError, "the integer does not fit " + phrase);
            return ScalarValue::from_signed(number);
        }

        auto value_from_python(PyObject* object, const properties::PropertyInfo& info) -> properties::Value
        {
            properties::Value value;
            value.category = info.category;
            switch (info.category)
            {
            case properties::Category::Scalar:
                value.scalar = scalar_from_python(object, info.scalar);
                break;
            case properties::Category::Name: {
                NameId id;
                if (fname_from_py(object, id))
                {
                    value.name = id;
                }
                else if (PyUnicode_Check(object))
                {
                    std::wstring text;
                    if (!wide_from_py(object, text)) throw PythonErrorSet{};
                    const auto created = bridge::name_from_string(text, true);
                    if (!created) throw UEError("the engine did not create the name");
                    value.name = *created;
                }
                else
                {
                    raise(PyExc_TypeError, std::string("a name property needs an FName or str, not ") + Py_TYPE(object)->tp_name);
                }
                break;
            }
            case properties::Category::String:
            case properties::Category::Text:
                if (!wide_from_py(object, value.text)) throw PythonErrorSet{};
                break;
            case properties::Category::Object:
                if (object == Py_None)
                {
                    value.object = objects::ObjectRef{};
                }
                else if (!object_ref_from_py(object, value.object))
                {
                    raise(PyExc_TypeError, std::string("an object property needs a UObject or None, not ") + Py_TYPE(object)->tp_name);
                }
                break;
            case properties::Category::SoftPath:
                if (object == Py_None) break; // null reference: empty path
                if (!PyUnicode_Check(object)) raise(PyExc_TypeError, std::string("a soft reference needs a path str or None, not ") + Py_TYPE(object)->tp_name);
                if (!wide_from_py(object, value.text)) throw PythonErrorSet{};
                break;
            case properties::Category::Delegate:
            case properties::Category::MulticastDelegate:
                break; // the engine layer refuses the write with UEError("delegate properties are read-only")
            case properties::Category::Struct:
            case properties::Category::Array:
            case properties::Category::FixedArray:
            case properties::Category::Map:
            case properties::Category::Set:
            case properties::Category::Unsupported:
                break;
            }
            return value;
        }

        auto get_property(const properties::Target& target, const std::wstring& name) -> PyObject*
        {
            const properties::Value value = properties::get(target, name);
            if (value.category == properties::Category::Array) return make_py_array(target, name);
            if (value.category == properties::Category::FixedArray) return make_py_fixed_array(target, name);
            if (value.category == properties::Category::Map) return make_py_map(target, name);
            if (value.category == properties::Category::Set) return make_py_set(target, name);
            return value_to_python(value);
        }

        auto describe_supported(const properties::Target& target, const std::wstring& name) -> properties::PropertyInfo
        {
            const auto info = properties::describe(target, name);
            if (!info) throw PropertyNotFoundError("the object has no property named '" + narrow(name) + "'");
            if (info->category == properties::Category::Unsupported)
            {
                throw UEError("property '" + narrow(name) + "' has type " + info->type_name + ", which is not supported yet");
            }
            return *info;
        }

        auto set_property(const properties::Target& target, const std::wstring& name, PyObject* object) -> void;

        // true when `source` is a Rotator struct being assigned to a Quat field: that is a conversion, not a copy
        auto is_rotator_for_quat(const properties::Target& target, const std::wstring& name, const properties::Target& source) -> bool
        {
            return source.is_struct() && properties::struct_name(source) == L"Rotator" && properties::struct_name(properties::get(target, name).structure) == L"Quat";
        }

        // obj.Struct = other_struct_view  (copies the whole struct) or obj.Struct = {"Field": value, ...}
        // (writes the listed fields, leaving the others alone).
        auto set_struct_property(const properties::Target& target, const std::wstring& name, PyObject* object) -> void
        {
            properties::Target source;
            if (struct_target_from_py(object, source) && !is_rotator_for_quat(target, name, source))
            {
                properties::copy_struct(target, name, source);
                return;
            }
            // A quaternion field also takes Euler angles: (pitch, yaw, roll), {"Pitch": .., "Yaw": .., "Roll": ..} or a Rotator struct.
            if (properties::struct_name(properties::get(target, name).structure) == L"Quat")
            {
                if (const auto q = rotator_like_to_quat(object))
                {
                    PyObject* fields = Py_BuildValue("{s:d,s:d,s:d,s:d}", "X", q->x, "Y", q->y, "Z", q->z, "W", q->w);
                    if (!fields) throw PythonErrorSet{};
                    try
                    {
                        properties_assign_fields(properties::get(target, name).structure, fields);
                    }
                    catch (...)
                    {
                        Py_DECREF(fields);
                        throw;
                    }
                    Py_DECREF(fields);
                    return;
                }
            }
            if (!PyDict_Check(object))
            {
                raise(PyExc_TypeError, std::string("a struct property needs a Struct or a dict of field values, not ") + Py_TYPE(object)->tp_name);
            }

            properties_assign_fields(properties::get(target, name).structure, object);
        }

        auto set_property(const properties::Target& target, const std::wstring& name, PyObject* object) -> void
        {
            const properties::PropertyInfo info = describe_supported(target, name);
            if (info.category == properties::Category::Struct)
            {
                set_struct_property(target, name, object);
                return;
            }
            if (info.category == properties::Category::Array)
            {
                array_assign(target, name, object);
                return;
            }
            if (info.category == properties::Category::FixedArray)
            {
                fixed_array_assign(target, name, object);
                return;
            }
            if (info.category == properties::Category::Map)
            {
                map_assign(target, name, object);
                return;
            }
            if (info.category == properties::Category::Set)
            {
                set_assign(target, name, object);
                return;
            }
            properties::set(target, name, value_from_python(object, info));
        }

        // Attribute names that are never properties: skip the engine lookup for Python's own probes.
        auto is_dunder(const std::wstring& name) -> bool
        {
            return name.size() >= 2 && name[0] == L'_' && name[1] == L'_';
        }
    }

    auto convert_value_to_python(const properties::Value& value) -> PyObject*
    {
        return value_to_python(value);
    }

    auto convert_value_from_python(PyObject* object, const properties::PropertyInfo& info) -> properties::Value
    {
        return value_from_python(object, info);
    }

    auto convert_element_from_python(PyObject* object, const properties::PropertyInfo& info) -> properties::Value
    {
        properties::Value converted;
        if (info.category == properties::Category::Struct)
        {
            converted.category = properties::Category::Struct;
            if (!struct_target_from_py(object, converted.structure))
            {
                PyErr_Format(PyExc_TypeError, "a struct needs a Struct, not %.200s", Py_TYPE(object)->tp_name);
                throw PythonErrorSet{};
            }
            return converted;
        }
        if (info.category == properties::Category::Unsupported || info.category == properties::Category::Array || info.category == properties::Category::Map ||
            info.category == properties::Category::Set ||
            info.category == properties::Category::Delegate || info.category == properties::Category::MulticastDelegate)
        {
            throw UEError("elements of type " + info.type_name + " are not supported yet");
        }
        return value_from_python(object, info);
    }

    auto properties_assign_fields(const properties::Target& target, PyObject* fields) -> void
    {
        PyObject* key = nullptr;
        PyObject* field_value = nullptr;
        Py_ssize_t position = 0;
        while (PyDict_Next(fields, &position, &key, &field_value))
        {
            std::wstring field;
            if (!wide_from_py(key, field)) throw PythonErrorSet{};
            set_property(target, field, field_value);
        }
    }

    auto property_assign(const properties::Target& target, const std::wstring& name, PyObject* value) -> void
    {
        set_property(target, name, value);
    }

    auto property_value_as_python(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        return guard_object([&]() -> PyObject* { return get_property(target, name); });
    }

    auto property_getattr(PyObject* self, PyObject* name, TargetOf target_of, const char* type_name, AttributeFallback fallback) -> PyObject*
    {
        PyObject* result = PyObject_GenericGetAttr(self, name);
        if (result || !PyErr_ExceptionMatches(PyExc_AttributeError)) return result;
        PyErr_Clear();
        return guard_object([&]() -> PyObject* {
            std::wstring wide;
            if (!wide_from_py(name, wide)) throw PythonErrorSet{};
            if (is_dunder(wide))
            {
                PyErr_Format(PyExc_AttributeError, "'%s' object has no attribute '%U'", type_name, name);
                throw PythonErrorSet{};
            }
            if (fallback && !properties::describe(target_of(self), wide))
            {
                if (PyObject* found = fallback(self, wide)) return found;
                if (PyErr_Occurred()) throw PythonErrorSet{};
                PyErr_Format(PyExc_AttributeError, "the object has no property or function named '%U'", name);
                throw PythonErrorSet{};
            }
            return get_property(target_of(self), wide);
        });
    }

    auto property_setattr(PyObject* self, PyObject* name, PyObject* value, TargetOf target_of, const char* type_name) -> int
    {
        if (!value)
        {
            PyErr_SetString(PyExc_TypeError, "properties cannot be deleted");
            return -1;
        }
        // Methods and other real attributes of the type are read-only.
        if (PyObject* existing = PyObject_GenericGetAttr(self, name))
        {
            Py_DECREF(existing);
            PyErr_Format(PyExc_AttributeError, "'%U' is a read-only attribute of %s", name, type_name);
            return -1;
        }
        if (!PyErr_ExceptionMatches(PyExc_AttributeError)) return -1;
        PyErr_Clear();

        return guard_int([&]() -> int {
            std::wstring wide;
            if (!wide_from_py(name, wide)) throw PythonErrorSet{};
            if (is_dunder(wide))
            {
                PyErr_Format(PyExc_AttributeError, "cannot set attribute '%U' on %s", name, type_name);
                throw PythonErrorSet{};
            }
            set_property(target_of(self), wide, value);
            return 0;
        });
    }

    auto property_get_method(PyObject* self, PyObject* name, TargetOf target_of) -> PyObject*
    {
        return guard_object([&]() -> PyObject* {
            std::wstring wide;
            if (!wide_from_py(name, wide)) throw PythonErrorSet{};
            return get_property(target_of(self), wide);
        });
    }

    auto property_set_method(PyObject* self, PyObject* args, TargetOf target_of) -> PyObject*
    {
        return guard_object([&]() -> PyObject* {
            PyObject* name = nullptr;
            PyObject* value = nullptr;
            if (!PyArg_ParseTuple(args, "OO:set_property_value", &name, &value)) throw PythonErrorSet{};
            std::wstring wide;
            if (!wide_from_py(name, wide)) throw PythonErrorSet{};
            set_property(target_of(self), wide, value);
            Py_RETURN_NONE;
        });
    }

    auto property_names_method(PyObject* self, TargetOf target_of) -> PyObject*
    {
        return guard_object([&]() -> PyObject* {
            PyObject* list = PyList_New(0);
            if (!list) throw PythonErrorSet{};
            for (const std::wstring& name : properties::property_names(target_of(self)))
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
        });
    }

    // The array as a list whose struct elements are dicts (like nested structs in to_dict()).
    static auto array_to_plain_list(const properties::Target& target, const std::wstring& name) -> PyObject*
    {
        PyObject* list = array_to_list(target, name);
        if (!list) return nullptr;
        const Py_ssize_t count = PyList_GET_SIZE(list);
        for (Py_ssize_t i = 0; i < count; ++i)
        {
            properties::Target element;
            if (!struct_target_from_py(PyList_GET_ITEM(list, i), element)) continue;
            PyObject* as_dict = properties_to_dict(element);
            if (!as_dict)
            {
                Py_DECREF(list);
                return nullptr;
            }
            PyList_SetItem(list, i, as_dict); // steals as_dict, releases the Struct
        }
        return list;
    }

    auto properties_to_dict(const properties::Target& target) -> PyObject*
    {
        PyObject* dict = PyDict_New();
        if (!dict) return nullptr;
        try
        {
            for (const std::wstring& name : properties::property_names(target))
            {
                const auto info = properties::describe(target, name);
                if (!info || info->category == properties::Category::Unsupported) continue;

                const properties::Value value = properties::get(target, name);
                PyObject* converted = value.category == properties::Category::Struct  ? properties_to_dict(value.structure)
                                      : value.category == properties::Category::Array ? array_to_plain_list(target, name)
                                      : value.category == properties::Category::FixedArray ? fixed_array_to_list(target, name)
                                      : value.category == properties::Category::Map   ? map_to_dict(target, name, true)
                                      : value.category == properties::Category::Set   ? set_to_list(target, name)
                                                                                      : value_to_python(value);
                PyObject* key = py_from_wide(name);
                if (!converted || !key || PyDict_SetItem(dict, key, converted) != 0)
                {
                    Py_XDECREF(converted);
                    Py_XDECREF(key);
                    Py_DECREF(dict);
                    return nullptr;
                }
                Py_DECREF(converted);
                Py_DECREF(key);
            }
        }
        catch (...)
        {
            Py_DECREF(dict);
            set_python_error(classify_current_exception());
            return nullptr;
        }
        return dict;
    }
}
