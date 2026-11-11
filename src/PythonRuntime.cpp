#include "PythonRuntime.hpp"

#include <fstream>
#include <iterator>
#include <string_view>
#include <vector>

#include <pyue/line_buffer.hpp>

#include "Gil.hpp"
#include "PythonApi.hpp"
#include "UeModule.hpp"

namespace pyue
{
    namespace
    {
        bool s_initialised_once = false;
        LogSink s_log_sink{};

        // --- sys.stdout / sys.stderr replacement ---------------------------------------------

        struct LogStreamObject
        {
            PyObject_HEAD
            bool is_error;
            pyue::LineBuffer* buffer;
        };

        void emit_lines(bool is_error, const std::vector<std::string>& lines)
        {
            if (!s_log_sink) return;
            for (const auto& line : lines)
            {
                try
                {
                    s_log_sink(is_error, line);
                }
                catch (...)
                {
                    // Logging must never propagate an exception into Python or the game.
                }
            }
        }

        PyObject* log_stream_write(PyObject* self_, PyObject* arg)
        {
            auto* self = reinterpret_cast<LogStreamObject*>(self_);
            if (!PyUnicode_Check(arg))
            {
                PyErr_Format(PyExc_TypeError, "write() argument must be str, not %.200s", Py_TYPE(arg)->tp_name);
                return nullptr;
            }
            // Lone surrogates cannot be encoded strictly; escape them instead of failing a print().
            PyObject* bytes = PyUnicode_AsEncodedString(arg, "utf-8", "backslashreplace");
            if (!bytes) return nullptr;
            emit_lines(self->is_error, self->buffer->append(std::string_view(PyBytes_AS_STRING(bytes), static_cast<size_t>(PyBytes_GET_SIZE(bytes)))));
            Py_DECREF(bytes);
            return PyLong_FromSsize_t(PyUnicode_GET_LENGTH(arg));
        }

        PyObject* log_stream_flush(PyObject* self_, PyObject*)
        {
            auto* self = reinterpret_cast<LogStreamObject*>(self_);
            emit_lines(self->is_error, self->buffer->flush());
            Py_RETURN_NONE;
        }

        PyObject* log_stream_false(PyObject*, PyObject*)
        {
            Py_RETURN_FALSE;
        }

        PyObject* log_stream_true(PyObject*, PyObject*)
        {
            Py_RETURN_TRUE;
        }

        PyObject* log_stream_get_encoding(PyObject*, void*)
        {
            return PyUnicode_FromString("utf-8");
        }

        PyObject* log_stream_get_errors(PyObject*, void*)
        {
            return PyUnicode_FromString("backslashreplace");
        }

        void log_stream_dealloc(PyObject* self_)
        {
            auto* self = reinterpret_cast<LogStreamObject*>(self_);
            delete self->buffer;
            PyTypeObject* type = Py_TYPE(self_);
            type->tp_free(self_);
            Py_DECREF(type);
        }

        PyMethodDef s_log_stream_methods[] = {
                {"write", log_stream_write, METH_O, "Buffer text and send complete lines to the UE4SS log."},
                {"flush", log_stream_flush, METH_NOARGS, "Send any unterminated text to the UE4SS log."},
                {"isatty", log_stream_false, METH_NOARGS, nullptr},
                {"writable", log_stream_true, METH_NOARGS, nullptr},
                {nullptr, nullptr, 0, nullptr},
        };

        PyGetSetDef s_log_stream_getset[] = {
                {"encoding", log_stream_get_encoding, nullptr, nullptr, nullptr},
                {"errors", log_stream_get_errors, nullptr, nullptr, nullptr},
                {nullptr, nullptr, nullptr, nullptr, nullptr},
        };

        PyType_Slot s_log_stream_slots[] = {
                {Py_tp_dealloc, reinterpret_cast<void*>(log_stream_dealloc)},
                {Py_tp_methods, s_log_stream_methods},
                {Py_tp_getset, s_log_stream_getset},
                {0, nullptr},
        };

        PyType_Spec s_log_stream_spec = {
                "ue4ss._LogStream",
                static_cast<int>(sizeof(LogStreamObject)),
                0,
                Py_TPFLAGS_DEFAULT,
                s_log_stream_slots,
        };

        // No tp_new: instances can only be created from C, which is what we want.
        auto install_log_streams() -> bool
        {
            PyObject* type_object = PyType_FromSpec(&s_log_stream_spec);
            if (!type_object) return false;
            auto* type = reinterpret_cast<PyTypeObject*>(type_object);

            bool ok = true;
            const struct { const char* name; bool is_error; } streams[] = {{"stdout", false}, {"stderr", true}};
            for (const auto& stream : streams)
            {
                PyObject* object = type->tp_alloc(type, 0);
                if (!object)
                {
                    ok = false;
                    break;
                }
                auto* log_stream = reinterpret_cast<LogStreamObject*>(object);
                log_stream->is_error = stream.is_error;
                log_stream->buffer = new pyue::LineBuffer();
                const int result = PySys_SetObject(stream.name, object);
                Py_DECREF(object);
                if (result != 0)
                {
                    ok = false;
                    break;
                }
            }
            Py_DECREF(type_object);
            return ok;
        }

        // Runs in a throwaway namespace so the helper names do not leak into __main__.
        auto check_error_mapping() -> bool
        {
            static const char* const source = R"PY(
import ue4ss

def expect(kind, exception_type):
    try:
        ue4ss._selftest_raise(kind)
    except exception_type:
        return
    except BaseException as e:
        raise AssertionError(f"{kind}: got {type(e).__name__}: {e}")
    raise AssertionError(f"{kind}: no exception")

assert issubclass(ue4ss.InvalidObjectError, ue4ss.UEError)
assert issubclass(ue4ss.UEError, Exception)
assert ue4ss._selftest_raise("none") is None
expect("ue", ue4ss.UEError)
expect("invalid", ue4ss.InvalidObjectError)
expect("property", AttributeError)
expect("std", ue4ss.UEError)
expect("unknown", ue4ss.UEError)
expect("memory", MemoryError)
expect("nonsense", ValueError)
)PY";
            PyObject* globals = PyDict_New();
            if (!globals) return false;
            PyObject* result = PyRun_String(source, Py_file_input, globals, globals);
            Py_DECREF(globals);
            if (!result)
            {
                PyErr_Print();
                return false;
            }
            Py_DECREF(result);
            return true;
        }

        auto set_string(PyConfig* config, wchar_t** field, const std::filesystem::path& value) -> PyStatus
        {
            return PyConfig_SetString(config, field, value.c_str());
        }

        auto build_config(PyConfig& config, const RuntimeConfig& runtime) -> PyStatus
        {
            PyStatus status;

            // UTF-8 mode is already on in an isolated config (PyPreConfig_InitIsolatedConfig).
            config.write_bytecode = 0; // keep __pycache__ out of the game folder
            config.site_import = 0;

            // Setting every path field keeps CPython from probing the environment for a prefix.
            status = set_string(&config, &config.home, runtime.home);
            if (PyStatus_Exception(status)) return status;
            status = set_string(&config, &config.prefix, runtime.home);
            if (PyStatus_Exception(status)) return status;
            status = set_string(&config, &config.base_prefix, runtime.home);
            if (PyStatus_Exception(status)) return status;
            status = set_string(&config, &config.exec_prefix, runtime.home);
            if (PyStatus_Exception(status)) return status;
            status = set_string(&config, &config.base_exec_prefix, runtime.home);
            if (PyStatus_Exception(status)) return status;
            status = set_string(&config, &config.executable, runtime.home / "main.dll");
            if (PyStatus_Exception(status)) return status;
            status = set_string(&config, &config.base_executable, runtime.home / "main.dll");
            if (PyStatus_Exception(status)) return status;

            for (const auto& path : runtime.module_search_paths)
            {
                status = PyWideStringList_Append(&config.module_search_paths, path.c_str());
                if (PyStatus_Exception(status)) return status;
            }
            config.module_search_paths_set = 1;
            return PyStatus_Ok();
        }
    }

    PythonRuntime::~PythonRuntime()
    {
        stop();
    }

    auto PythonRuntime::start(const RuntimeConfig& runtime) -> bool
    {
        if (m_running)
        {
            m_error = "interpreter is already running";
            return false;
        }
        if (s_initialised_once)
        {
            m_error = "the interpreter was already finalized once in this process and cannot be restarted";
            return false;
        }

        // Built-in modules must be registered before initialisation.
        if (PyImport_AppendInittab("ue4ss", &init_ue4ss_module) == -1)
        {
            m_error = "PyImport_AppendInittab(ue4ss) failed";
            return false;
        }

        PyConfig config;
        PyConfig_InitIsolatedConfig(&config);

        PyStatus status = build_config(config, runtime);
        if (!PyStatus_Exception(status))
        {
            status = Py_InitializeFromConfig(&config);
        }
        PyConfig_Clear(&config);

        s_initialised_once = true;
        if (PyStatus_Exception(status))
        {
            m_error = std::string(status.func ? status.func : "") + ": " + (status.err_msg ? status.err_msg : "unknown error");
            return false;
        }

        m_running = true;
        g_python_alive.store(true, std::memory_order_release);
        if (!install_log_streams())
        {
            PyErr_Clear();
            m_error = "failed to install the stdout/stderr redirect";
            stop();
            return false;
        }
        m_error.clear();
        return true;
    }

    auto PythonRuntime::set_log_sink(LogSink sink) -> void
    {
        s_log_sink = std::move(sink);
    }

    auto PythonRuntime::stop() -> void
    {
        if (!m_running) return;
        g_python_alive.store(false, std::memory_order_release); // PyObjectRef now leaks instead of touching Python
        if (m_gil_released)
        {
            PyEval_RestoreThread(static_cast<PyThreadState*>(m_main_thread_state));
            m_gil_released = false;
            m_main_thread_state = nullptr;
        }
        Py_FinalizeEx();
        m_running = false;
    }

    auto PythonRuntime::release_gil() -> void
    {
        if (!m_running || m_gil_released) return;
        m_main_thread_state = PyEval_SaveThread();
        m_gil_released = true;
    }

    auto PythonRuntime::run_snippet(const char* code) -> bool
    {
        if (!python_alive())
        {
            m_error = "interpreter is not running";
            return false;
        }
        try
        {
            GilGuard gil;
            const bool ok = PyRun_SimpleString(code) == 0;
            if (!ok) m_error = "the snippet raised an exception (see the traceback above)";
            return ok;
        }
        catch (...)
        {
            m_error = "C++ exception while running the snippet";
            return false;
        }
    }

    auto PythonRuntime::python_version() const -> std::string
    {
        return m_running ? std::string(Py_GetVersion()) : std::string();
    }

    auto PythonRuntime::run_file(const std::filesystem::path& script) -> bool
    {
        if (!m_running)
        {
            m_error = "interpreter is not running";
            return false;
        }

        try
        {
            GilGuard gil; // run_file may be called from any thread
            std::ifstream file(script, std::ios::binary);
            if (!file)
            {
                m_error = "could not open the script file";
                return false;
            }
            const std::string source((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());

            PyObject* filename = PyUnicode_FromWideChar(script.c_str(), -1);
            if (!filename)
            {
                PyErr_Print();
                m_error = "could not convert the script path";
                return false;
            }

            PyObject* code = Py_CompileStringObject(source.c_str(), filename, Py_file_input, nullptr, -1);
            if (!code)
            {
                Py_DECREF(filename);
                PyErr_Print(); // SyntaxError: nothing here can be SystemExit
                m_error = "the script failed to compile";
                return false;
            }

            PyObject* main_module = PyImport_AddModule("__main__"); // borrowed
            PyObject* globals = main_module ? PyModule_GetDict(main_module) : nullptr; // borrowed
            if (!globals || PyDict_SetItemString(globals, "__file__", filename) != 0)
            {
                Py_DECREF(code);
                Py_DECREF(filename);
                PyErr_Print();
                m_error = "could not prepare the __main__ namespace";
                return false;
            }

            PyObject* result = PyEval_EvalCode(code, globals, globals);
            Py_DECREF(code);
            Py_DECREF(filename);

            if (!result)
            {
                if (PyErr_ExceptionMatches(PyExc_SystemExit))
                {
                    // PyErr_Print() would call exit() for SystemExit and take the game down with it.
                    PyErr_Clear();
                    m_error = "the script called sys.exit()";
                }
                else
                {
                    PyErr_Print();
                    m_error = "the script raised an exception (see the traceback above)";
                }
                return false;
            }
            Py_DECREF(result);
            m_error.clear();
            return true;
        }
        catch (const std::exception& e)
        {
            m_error = std::string("C++ exception while running the script: ") + e.what();
            return false;
        }
        catch (...)
        {
            m_error = "unknown C++ exception while running the script";
            return false;
        }
    }

    auto PythonRuntime::self_check() -> bool
    {
        if (!m_running)
        {
            m_error = "interpreter is not running";
            return false;
        }

        PyObject* module = PyImport_ImportModule("ue4ss");
        if (!module)
        {
            PyErr_Clear();
            m_error = "import ue4ss failed";
            return false;
        }
        PyObject* version = PyObject_GetAttrString(module, "__version__");
        Py_DECREF(module);
        if (!version)
        {
            PyErr_Clear();
            m_error = "ue4ss.__version__ missing";
            return false;
        }
        Py_DECREF(version);

        // The stdlib must come from python312.zip; importing json exercises the zip importer.
        PyObject* json = PyImport_ImportModule("json");
        if (!json)
        {
            PyErr_Clear();
            m_error = "import json failed (is python312.zip next to main.dll?)";
            return false;
        }
        Py_DECREF(json);

        if (!check_error_mapping())
        {
            m_error = "the C++ -> Python exception mapping is wrong (see the traceback above)";
            return false;
        }

        if (PyRun_SimpleString("print('stdout redirect ok')") != 0)
        {
            PyErr_Clear();
            m_error = "print() through the stdout redirect failed";
            return false;
        }

        m_error.clear();
        return true;
    }
}
