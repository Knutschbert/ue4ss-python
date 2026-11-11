#pragma once

#include "PythonApi.hpp"

// Python entry points for key bindings: ue4ss.register_key_down / unregister_key_down.
namespace pyue
{
    // register_key_down(key, callback, modifiers=()) -> id; callback() runs on the game thread when the key goes down
    PyObject* py_register_key_down(PyObject* self, PyObject* args, PyObject* kwargs);

    // unregister_key_down(id)
    PyObject* py_unregister_key_down(PyObject* self, PyObject* args);

    // Test only (ue4ss._selftest_fire_key): behaves as if the key (with exactly these modifiers) went down. Returns how many
    // bindings will run.
    PyObject* py_selftest_fire_key(PyObject* self, PyObject* args);

    // Removes every binding. The native UE4SS handlers cannot be removed and then do nothing. Call before shutting Python down.
    auto unregister_all_key_bindings() -> void;
}
