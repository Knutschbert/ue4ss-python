#include "Reload.hpp"

#include <mutex>

#include <DynamicOutput/DynamicOutput.hpp>

#include <pyue/reload_gate.hpp>

#include "GameThreadExecutor.hpp"
#include "Gil.hpp"
#include "PyHooks.hpp"
#include "PyKeys.hpp"
#include "PyNewObjects.hpp"
#include "PyText.hpp"

using namespace RC;

namespace pyue::reload
{
    namespace
    {
        ReloadGate s_gate;
        std::mutex s_mutex;
        std::wstring s_scripts_dir;
        MainRunner s_run_main;

        auto widen(const std::string& text) -> StringType
        {
            return StringType(text.begin(), text.end());
        }

        // Python side: drop the modules loaded from the scripts folder and the names main.py left behind.
        auto forget_script_modules(const std::wstring& scripts_dir) -> void
        {
            GilGuard gil;
            PyObject* module = PyImport_ImportModule("ue4ss");
            PyObject* function = module ? PyObject_GetAttrString(module, "_reload_cleanup") : nullptr;
            PyObject* folder = PyUnicode_FromWideChar(scripts_dir.c_str(), -1);
            PyObject* removed = (function && folder) ? PyObject_CallFunctionObjArgs(function, folder, nullptr) : nullptr;
            if (!removed)
            {
                PyErr_Print();
            }
            else
            {
                Output::send<LogLevel::Verbose>(STR("PythonScripting: reload dropped {} script module(s)\n"), static_cast<long long>(PyList_Check(removed) ? PyList_GET_SIZE(removed) : 0));
            }
            Py_XDECREF(removed);
            Py_XDECREF(folder);
            Py_XDECREF(function);
            Py_XDECREF(module);
        }

        // Runs on the game thread, from the executor's queue (never inside a hook or another script callback).
        auto do_reload() -> void
        {
            if (!python_alive()) return;
            MainRunner run_main;
            std::wstring scripts_dir;
            {
                std::lock_guard lock{s_mutex};
                run_main = s_run_main;
                scripts_dir = s_scripts_dir;
            }
            if (!run_main) return;

            const std::uint32_t generation = s_gate.begin();
            Output::send<LogLevel::Verbose>(STR("PythonScripting: reloading, generation {}\n"), static_cast<long long>(generation));
            unregister_all_hooks();                      // hooks and replacements
            unregister_all_new_object_notifications();
            unregister_all_key_bindings();
            game_thread_executor::clear_pending();       // queued and delayed tasks of the old scripts
            forget_script_modules(scripts_dir);

            std::string error;
            if (run_main(error))
            {
                Output::send<LogLevel::Verbose>(STR("PythonScripting: reload finished, main.py ran (generation {})\n"), static_cast<long long>(generation));
            }
            else
            {
                Output::send<LogLevel::Error>(STR("PythonScripting: reload: main.py failed: {}\n"), widen(error));
            }
        }
    }

    auto configure(std::wstring scripts_dir, MainRunner run_main) -> void
    {
        std::lock_guard lock{s_mutex};
        s_scripts_dir = std::move(scripts_dir);
        s_run_main = std::move(run_main);
    }

    auto mark_started() -> void
    {
        s_gate.start();
    }

    PyObject* py_reload(PyObject*, PyObject*)
    {
        if (s_gate.request()) game_thread_executor::post([] { do_reload(); });
        Py_RETURN_NONE;
    }

    PyObject* py_script_generation(PyObject*, PyObject*)
    {
        return PyLong_FromUnsignedLong(s_gate.generation());
    }
}
