#include "SlowCallback.hpp"

#include <chrono>

#include <pyue/slow_callback.hpp>

namespace pyue::slow
{
    namespace
    {
        SlowCallbackPolicy s_policy;

        auto now_ns() -> std::int64_t
        {
            return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
        }
    }

    auto set_threshold_ms(std::int64_t milliseconds) -> void
    {
        s_policy.set_threshold_ms(milliseconds);
    }

    auto threshold_ms() -> std::int64_t
    {
        return s_policy.threshold_ms();
    }

    Scope::Scope(const char* kind, Describe describe, const void* subject) : m_kind(kind), m_describe(describe), m_subject(subject), m_start_ns(now_ns()) {}

    Scope::~Scope()
    {
        // Cheap on the common path: one clock read and one comparison. Everything else runs only for a slow callback.
        const auto elapsed = std::chrono::nanoseconds(now_ns() - m_start_ns);
        const std::int64_t limit = s_policy.threshold_ms();
        if (limit <= 0 || elapsed < std::chrono::milliseconds(limit)) return;
        try
        {
            std::string label = m_kind;
            if (m_describe)
            {
                try
                {
                    label += ": " + m_describe(m_subject);
                }
                catch (...)
                {
                }
            }
            if (const auto message = s_policy.check(label, elapsed)) PySys_WriteStderr("%s\n", message->c_str());
        }
        catch (...)
        {
            // a failing warning must never disturb the callback's caller
        }
    }

    PyObject* py_set_slow_callback_warning(PyObject*, PyObject* args)
    {
        PyObject* value = nullptr;
        if (!PyArg_ParseTuple(args, "O:set_slow_callback_warning", &value)) return nullptr;
        if (!PyLong_Check(value) || PyBool_Check(value))
        {
            PyErr_Format(PyExc_TypeError, "set_slow_callback_warning() needs an int number of milliseconds, not %.200s", Py_TYPE(value)->tp_name);
            return nullptr;
        }
        const long long milliseconds = PyLong_AsLongLong(value);
        if (milliseconds == -1 && PyErr_Occurred()) return nullptr;
        if (milliseconds < 0)
        {
            PyErr_SetString(PyExc_ValueError, "the threshold must not be negative (0 switches the warnings off)");
            return nullptr;
        }
        set_threshold_ms(milliseconds);
        Py_RETURN_NONE;
    }
}
