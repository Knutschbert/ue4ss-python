#include "PyRotation.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <string>

#include <pyue/errors.hpp>

#include "PropertyApi.hpp"
#include "PyStruct.hpp"

namespace pyue
{
    namespace
    {
        auto lower(std::string text) -> std::string
        {
            std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            return text;
        }

        auto number(PyObject* object, const char* what) -> double
        {
            if (PyUnicode_Check(object) || PyBytes_Check(object))
            {
                PyErr_Format(PyExc_TypeError, "%s must be a number, not %.200s", what, Py_TYPE(object)->tp_name);
                throw PythonErrorSet{};
            }
            const double value = PyFloat_AsDouble(object);
            if (value == -1.0 && PyErr_Occurred()) throw PythonErrorSet{};
            return value;
        }

        // "unreal" -> nullopt (the rotator convention); an axis order -> its axes. Raises ValueError for anything else.
        auto parse_order(const std::string& order) -> std::optional<std::array<int, 3>>
        {
            if (lower(order) == "unreal") return std::nullopt;
            const auto axes = rotation::parse_axes(order);
            if (!axes)
            {
                PyErr_Format(PyExc_ValueError, "unknown rotation order '%.100s': use \"unreal\" (pitch, yaw, roll) or the three axes in some order, such as \"xyz\" or \"zyx\"", order.c_str());
                throw PythonErrorSet{};
            }
            return axes;
        }

        auto read_order(PyObject* object) -> std::optional<std::array<int, 3>>
        {
            if (!PyUnicode_Check(object))
            {
                PyErr_Format(PyExc_TypeError, "order must be a str, not %.200s", Py_TYPE(object)->tp_name);
                throw PythonErrorSet{};
            }
            return parse_order(PyUnicode_AsUTF8(object));
        }

        auto flag(PyObject* object, const char* what) -> bool
        {
            if (!PyBool_Check(object))
            {
                PyErr_Format(PyExc_TypeError, "%s must be True or False, not %.200s", what, Py_TYPE(object)->tp_name);
                throw PythonErrorSet{};
            }
            return object == Py_True;
        }

        // X, Y, Z, W of a quaternion given as a dict, a sequence of four numbers, or a struct with those fields.
        auto read_quat(PyObject* object) -> rotation::Quat
        {
            std::array<double, 4> values{};
            if (PyDict_Check(object))
            {
                static const char* names[4] = {"x", "y", "z", "w"};
                std::array<bool, 4> found{};
                PyObject* key = nullptr;
                PyObject* item = nullptr;
                Py_ssize_t position = 0;
                while (PyDict_Next(object, &position, &key, &item))
                {
                    if (!PyUnicode_Check(key)) continue;
                    const std::string name = lower(PyUnicode_AsUTF8(key));
                    for (int i = 0; i < 4; ++i)
                    {
                        if (name == names[i])
                        {
                            values[static_cast<std::size_t>(i)] = number(item, names[i]);
                            found[static_cast<std::size_t>(i)] = true;
                        }
                    }
                }
                if (!(found[0] && found[1] && found[2] && found[3]))
                {
                    PyErr_SetString(PyExc_ValueError, "a quaternion dict needs X, Y, Z and W");
                    throw PythonErrorSet{};
                }
            }
            else if (PyList_Check(object) || PyTuple_Check(object))
            {
                if (PySequence_Size(object) != 4)
                {
                    PyErr_SetString(PyExc_ValueError, "a quaternion sequence needs four numbers (x, y, z, w)");
                    throw PythonErrorSet{};
                }
                for (Py_ssize_t i = 0; i < 4; ++i)
                {
                    PyObject* item = PySequence_GetItem(object, i);
                    if (!item) throw PythonErrorSet{};
                    try
                    {
                        values[static_cast<std::size_t>(i)] = number(item, "a quaternion component");
                    }
                    catch (...)
                    {
                        Py_DECREF(item);
                        throw;
                    }
                    Py_DECREF(item);
                }
            }
            else
            {
                static const char* names[4] = {"X", "Y", "Z", "W"};
                for (int i = 0; i < 4; ++i)
                {
                    PyObject* item = PyObject_GetAttrString(object, names[i]);
                    if (!item)
                    {
                        PyErr_Clear();
                        PyErr_Format(PyExc_TypeError, "a quaternion is a dict, a sequence of four numbers or a struct with X, Y, Z and W, not %.200s", Py_TYPE(object)->tp_name);
                        throw PythonErrorSet{};
                    }
                    try
                    {
                        values[static_cast<std::size_t>(i)] = number(item, names[i]);
                    }
                    catch (...)
                    {
                        Py_DECREF(item);
                        throw;
                    }
                    Py_DECREF(item);
                }
            }
            return rotation::Quat{values[0], values[1], values[2], values[3]};
        }

        auto quat_dict(const rotation::Quat& q) -> PyObject*
        {
            return Py_BuildValue("{s:d,s:d,s:d,s:d}", "X", q.x, "Y", q.y, "Z", q.z, "W", q.w);
        }
    }

    PyObject* py_euler_to_quat(PyObject*, PyObject* args, PyObject* kwargs)
    {
        return guard_object([&]() -> PyObject* {
            static const char* keywords[] = {"a", "b", "c", "order", "degrees", nullptr};
            PyObject *a = nullptr, *b = nullptr, *c = nullptr;
            PyObject* order = nullptr;
            PyObject* degrees = nullptr;
            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOO|OO:euler_to_quat", const_cast<char**>(keywords), &a, &b, &c, &order, &degrees)) throw PythonErrorSet{};
            const auto axes = order ? read_order(order) : std::nullopt;
            const bool use_degrees = degrees ? flag(degrees, "degrees") : true;
            const double first = number(a, "the first angle"), second = number(b, "the second angle"), third = number(c, "the third angle");
            const rotation::Quat q = axes ? rotation::from_axes(*axes, first, second, third, use_degrees) : rotation::from_rotator(first, second, third, use_degrees);
            return quat_dict(q);
        });
    }

    PyObject* py_quat_to_euler(PyObject*, PyObject* args, PyObject* kwargs)
    {
        return guard_object([&]() -> PyObject* {
            static const char* keywords[] = {"quat", "order", "degrees", nullptr};
            PyObject* quat = nullptr;
            PyObject* order = nullptr;
            PyObject* degrees = nullptr;
            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OO:quat_to_euler", const_cast<char**>(keywords), &quat, &order, &degrees)) throw PythonErrorSet{};
            const auto axes = order ? read_order(order) : std::nullopt;
            const bool use_degrees = degrees ? flag(degrees, "degrees") : true;
            const rotation::Quat q = read_quat(quat);
            const std::array<double, 3> angles = axes ? rotation::to_axes(q, *axes, use_degrees) : rotation::to_rotator(rotation::normalized(q), use_degrees);
            return Py_BuildValue("(ddd)", angles[0], angles[1], angles[2]);
        });
    }

    auto rotator_like_to_quat(PyObject* object) -> std::optional<rotation::Quat>
    {
        double pitch = 0, yaw = 0, roll = 0;
        objects::properties::Target target;
        if (struct_target_from_py(object, target))
        {
            if (!target.is_struct() || objects::properties::struct_name(target) != L"Rotator") return std::nullopt;
            const char* names[3] = {"Pitch", "Yaw", "Roll"};
            double* out[3] = {&pitch, &yaw, &roll};
            for (int i = 0; i < 3; ++i)
            {
                PyObject* item = PyObject_GetAttrString(object, names[i]);
                if (!item) throw PythonErrorSet{};
                try
                {
                    *out[i] = number(item, names[i]);
                }
                catch (...)
                {
                    Py_DECREF(item);
                    throw;
                }
                Py_DECREF(item);
            }
            return rotation::from_rotator(pitch, yaw, roll);
        }
        if (PyList_Check(object) || PyTuple_Check(object))
        {
            if (PySequence_Size(object) != 3)
            {
                PyErr_SetString(PyExc_ValueError, "a rotation given as a sequence needs three numbers (pitch, yaw, roll in degrees)");
                throw PythonErrorSet{};
            }
            double* out[3] = {&pitch, &yaw, &roll};
            for (Py_ssize_t i = 0; i < 3; ++i)
            {
                PyObject* item = PySequence_GetItem(object, i);
                if (!item) throw PythonErrorSet{};
                try
                {
                    *out[i] = number(item, "a rotation angle");
                }
                catch (...)
                {
                    Py_DECREF(item);
                    throw;
                }
                Py_DECREF(item);
            }
            return rotation::from_rotator(pitch, yaw, roll);
        }
        if (PyDict_Check(object) && PyDict_Size(object) > 0)
        {
            bool has_rotator_key = false, has_quat_key = false;
            PyObject* key = nullptr;
            PyObject* item = nullptr;
            Py_ssize_t position = 0;
            while (PyDict_Next(object, &position, &key, &item))
            {
                if (!PyUnicode_Check(key)) return std::nullopt;
                const std::string name = lower(PyUnicode_AsUTF8(key));
                if (name == "pitch" || name == "yaw" || name == "roll") has_rotator_key = true;
                else if (name == "x" || name == "y" || name == "z" || name == "w") has_quat_key = true;
            }
            if (!has_rotator_key) return std::nullopt;
            if (has_quat_key)
            {
                PyErr_SetString(PyExc_ValueError, "give either Pitch/Yaw/Roll or X/Y/Z/W for a rotation, not both");
                throw PythonErrorSet{};
            }
            position = 0;
            while (PyDict_Next(object, &position, &key, &item))
            {
                const std::string name = lower(PyUnicode_AsUTF8(key));
                if (name == "pitch") pitch = number(item, "Pitch");
                else if (name == "yaw") yaw = number(item, "Yaw");
                else if (name == "roll") roll = number(item, "Roll");
                else
                {
                    PyErr_Format(PyExc_ValueError, "unknown rotation key '%.100s' (expected Pitch, Yaw and Roll)", name.c_str());
                    throw PythonErrorSet{};
                }
            }
            return rotation::from_rotator(pitch, yaw, roll);
        }
        return std::nullopt;
    }
}
