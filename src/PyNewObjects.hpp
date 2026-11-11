#pragma once

#include "PythonApi.hpp"

// Python entry points for object-creation notifications: ue4ss.notify_on_new_object / unregister_notify_on_new_object.
namespace pyue
{
    // notify_on_new_object(class_path, callback) -> id; callback(obj) runs on the game thread, shortly after creation
    PyObject* py_notify_on_new_object(PyObject* self, PyObject* args);

    // unregister_notify_on_new_object(id)
    PyObject* py_unregister_notify_on_new_object(PyObject* self, PyObject* args);

    // Removes every subscription. Call before shutting Python down.
    auto unregister_all_new_object_notifications() -> void;
}
