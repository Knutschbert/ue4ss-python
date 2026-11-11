#pragma once

#include "PythonApi.hpp"

// Python entry points for hooking engine functions: ue4ss.register_hook / unregister_hook.
namespace pyue
{
    // Creates ue4ss.NULL, the value a post-hook returns to set a null object reference as the result.
    auto init_hook_types(PyObject* module) -> bool;

    // register_hook(path, pre=None, post=None) -> (pre_id, post_id); pre(context, params), post(context, params, result)
    PyObject* py_register_hook(PyObject* self, PyObject* args, PyObject* kwargs);

    // unregister_hook((pre_id, post_id)) or unregister_hook(pre_id, post_id)
    PyObject* py_unregister_hook(PyObject* self, PyObject* args);

    // register_replacement(path, callback) -> id: callback(context, params) runs INSTEAD of the function and returns its result,
    // or ue4ss.CALL_ORIGINAL to let the function run. Only calls through ProcessEvent are seen (see HookApi.hpp).
    PyObject* py_register_replacement(PyObject* self, PyObject* args);

    // unregister_replacement(id)
    PyObject* py_unregister_replacement(PyObject* self, PyObject* args);

    // Removes every hook (best effort: needs the game thread; otherwise they stay installed but inert, because the
    // trampoline checks that the interpreter is still alive). Call before shutting Python down.
    auto unregister_all_hooks() -> void;

    // bind_event(object, name, callback) -> id: bind a Python callback to a multicast delegate property (an Event
    // Dispatcher) on the object. The callback is called (context, params) each time the delegate broadcasts.
    PyObject* py_bind_event(PyObject* self, PyObject* args, PyObject* kwargs);

    // unbind_event(id): stop delivering broadcasts to that binding.
    PyObject* py_unbind_event(PyObject* self, PyObject* args);
}
