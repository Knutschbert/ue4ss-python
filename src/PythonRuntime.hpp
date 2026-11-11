#pragma once

#include <filesystem>
#include <functional>
#include <string>

#include <pyue/runtime_config.hpp>

namespace pyue
{
    // Owns the embedded CPython interpreter. Deliberately free of UE4SS headers so that
    // Python.h (which defines many macros) is only ever seen by PythonRuntime.cpp.
    //
    // An interpreter can be initialised once per process: Py_FinalizeEx followed by a second
    // Py_InitializeFromConfig is unreliable with C extension modules, so start() refuses to run
    // again after stop(). Script reload is handled above this layer (see the hot reload ticket).
    // Receives complete lines written to sys.stdout (is_error == false) or sys.stderr (true).
    // Called with the GIL held, from whichever thread wrote; must not call back into Python.
    using LogSink = std::function<void(bool is_error, const std::string& line)>;

    class PythonRuntime
    {
    public:
        PythonRuntime() = default;
        ~PythonRuntime();
        PythonRuntime(const PythonRuntime&) = delete;
        auto operator=(const PythonRuntime&) -> PythonRuntime& = delete;

        // Set before start(). sys.stdout/sys.stderr are replaced by streams feeding this sink.
        auto set_log_sink(LogSink sink) -> void;

        // Returns false on failure; the reason is in last_error().
        auto start(const RuntimeConfig& config) -> bool;
        auto stop() -> void;

        auto is_running() const -> bool { return m_running; }
        auto last_error() const -> const std::string& { return m_error; }

        // Compiles and runs a script in the __main__ namespace. A Python exception is printed as a
        // full traceback to sys.stderr (the UE4SS log) and makes this return false; sys.exit() is
        // caught and never terminates the game. last_error() holds a one-line reason.
        auto run_file(const std::filesystem::path& script) -> bool;

        // Releases the GIL held by the thread that started the interpreter, so that every later
        // entry into Python (any thread) takes it with a GilGuard. Call once, after the startup
        // script has run. Idempotent.
        auto release_gil() -> void;

        // Runs a code snippet under a GilGuard on the calling thread (any thread). Errors print a
        // traceback to the log and make this return false.
        auto run_snippet(const char* code) -> bool;

        // "3.12.10 (tags/... [MSC v.1943 64 bit (AMD64)]"
        auto python_version() const -> std::string;

        // Imports the built-in `ue4ss` module and checks it. Returns false and sets last_error() on failure.
        auto self_check() -> bool;

    private:
        bool m_running{};
        bool m_gil_released{};
        void* m_main_thread_state{}; // PyThreadState* saved by release_gil(), restored in stop()
        std::string m_error{};
    };
}
