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

        // Managed runtimes live in the shared ue4ss package, not main.py globals.
        // Look up only an already-loaded submodule: legacy scripts must not import it.
        auto notify_managed_runtime_before_reload() -> void
        {
            GilGuard gil;
            PyObject* modules = PyImport_GetModuleDict();
            PyObject* runtime = modules ? PyDict_GetItemString(modules, "ue4ss.runtime") : nullptr; // borrowed
            if (!runtime) return;

            PyObject* function = PyObject_GetAttrString(runtime, "_before_global_reload");
            if (!function)
            {
                PyErr_Clear(); // an older or partially imported runtime has no private hook
                return;
            }
            PyObject* result = PyObject_CallNoArgs(function);
            if (!result)
            {
                PyErr_Print();
            }
            else
            {
                PyObject* pending = PyDict_Check(result) ? PyDict_GetItemString(result, "pending_workers") : nullptr;
                if (pending && PyLong_Check(pending))
                {
                    const long count = PyLong_AsLong(pending);
                    Output::send<LogLevel::Verbose>(
                            STR("PythonScripting: managed reload signalled; {} worker(s) still stopping\n"),
                            static_cast<long long>(count));
                }
                PyObject* errors = PyDict_Check(result) ? PyDict_GetItemString(result, "errors") : nullptr;
                if (errors && PyList_Check(errors) && PyList_GET_SIZE(errors) > 0)
                {
                    PyObject* text = PyObject_Str(errors);
                    const char* utf8 = text ? PyUnicode_AsUTF8(text) : nullptr;
                    if (utf8)
                    {
                        Output::send<LogLevel::Error>(
                                STR("PythonScripting: managed reload cleanup errors: {}\n"), widen(utf8));
                    }
                    else
                    {
                        PyErr_Clear();
                    }
                    Py_XDECREF(text);
                }
            }
            Py_XDECREF(result);
            Py_DECREF(function);
        }

        auto resume_future_admission_after_queue_clear() -> void
        {
            GilGuard gil;
            PyObject* modules = PyImport_GetModuleDict();
            PyObject* ue4ss = modules ? PyDict_GetItemString(modules, "ue4ss") : nullptr; // borrowed
            PyObject* function = ue4ss ? PyObject_GetAttrString(ue4ss, "_resume_pending_futures") : nullptr;
            if (!function)
            {
                PyErr_Clear();
                return;
            }
            PyObject* result = PyObject_CallNoArgs(function);
            if (!result) PyErr_Print();
            Py_XDECREF(result);
            Py_DECREF(function);
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

            notify_managed_runtime_before_reload();
            const std::uint32_t generation = s_gate.begin();
            Output::send<LogLevel::Verbose>(STR("PythonScripting: reloading, generation {}\n"), static_cast<long long>(generation));
            unregister_all_hooks();                      // hooks and replacements
            unregister_all_new_object_notifications();
            unregister_all_key_bindings();
            game_thread_executor::clear_pending();       // queued and delayed tasks of the old scripts
            forget_script_modules(scripts_dir);
            resume_future_admission_after_queue_clear();

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
