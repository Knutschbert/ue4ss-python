#pragma once

#include <functional>
#include <string>

#include "PythonApi.hpp"

// Script reload (ue4ss.reload): unregister everything the scripts registered, forget their modules and run main.py again.
namespace pyue::reload
{
    using MainRunner = std::function<bool(std::string& error)>;

    // Tells the reloader where the scripts live and how to run main.py. Call once at startup, before the first run of main.py.
    auto configure(std::wstring scripts_dir, MainRunner run_main) -> void;

    // main.py is about to run for the first time: that is generation 1.
    auto mark_started() -> void;

    // reload() -> None: any thread; the reload itself runs on the game thread at the next tick (several calls make one reload)
    PyObject* py_reload(PyObject* self, PyObject* args);

    // script_generation() -> int: 1 for the first run of main.py, plus one for every reload
    PyObject* py_script_generation(PyObject* self, PyObject* args);
}
