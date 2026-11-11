#pragma once

#include <optional>

#include <pyue/rotation.hpp>

#include "PythonApi.hpp"

// ue4ss.euler_to_quat / ue4ss.quat_to_euler, and the rotator shorthand accepted by quaternion fields.
namespace pyue
{
    // euler_to_quat(a, b, c, order="unreal", degrees=True) -> {"X", "Y", "Z", "W"}
    PyObject* py_euler_to_quat(PyObject* self, PyObject* args, PyObject* kwargs);

    // quat_to_euler(quat, order="unreal", degrees=True) -> (a, b, c)
    PyObject* py_quat_to_euler(PyObject* self, PyObject* args, PyObject* kwargs);

    // If `object` describes a rotation by Euler angles, the quaternion for it: a sequence of three numbers (pitch, yaw, roll in degrees),
    // a dict with only Pitch / Yaw / Roll keys (any case, missing ones are 0), or a Rotator struct. nullopt if it is anything else
    // (a normal X/Y/Z/W dict, a Quat struct, ...). Throws PythonErrorSet with an exception set for a malformed rotator.
    auto rotator_like_to_quat(PyObject* object) -> std::optional<rotation::Quat>;
}
