#pragma once

#include <atomic>
#include <utility>

#include "PythonApi.hpp"

// GIL helpers. The rules they implement are in docs/THREADING.md.
namespace pyue
{
    // True between a successful interpreter start and the beginning of shutdown. Native code that
    // may run late (destructors, queued tasks) checks it before touching Python.
    inline std::atomic<bool> g_python_alive{false};

    inline auto python_alive() -> bool
    {
        return g_python_alive.load(std::memory_order_acquire);
    }

    // Takes the GIL for the current scope, on any thread (creating a thread state if the thread is new
    // to Python). Safe to nest and safe if the thread already holds the GIL.
    // Use at every entry from the engine into Python: hook trampolines, tick callbacks, queued tasks.
    class GilGuard
    {
    public:
        GilGuard() : m_state(PyGILState_Ensure()) {}
        ~GilGuard() { PyGILState_Release(m_state); }
        GilGuard(const GilGuard&) = delete;
        auto operator=(const GilGuard&) -> GilGuard& = delete;

    private:
        PyGILState_STATE m_state;
    };

    // Releases the GIL for the current scope. Use around calls into the engine (ProcessEvent, spawn,
    // load, ...) so a thread the engine waits on can run a Python hook meanwhile. The thread must hold
    // the GIL when this is constructed.
    class GilRelease
    {
    public:
        GilRelease() : m_state(PyEval_SaveThread()) {}
        ~GilRelease() { PyEval_RestoreThread(m_state); }
        GilRelease(const GilRelease&) = delete;
        auto operator=(const GilRelease&) -> GilRelease& = delete;

    private:
        PyThreadState* m_state;
    };

    // Owning reference to a Python object that can be created, copied and destroyed on any thread:
    // every reference-count change takes the GIL. Native objects (hook records, queued callbacks)
    // hold their Python callables through this type. Destruction after the interpreter is gone leaks
    // the reference on purpose instead of touching freed memory.
    class PyObjectRef
    {
    public:
        PyObjectRef() = default;

        // Takes over one existing reference (e.g. the result of a call returning a new reference).
        static auto steal(PyObject* object) -> PyObjectRef
        {
            PyObjectRef ref;
            ref.m_object = object;
            return ref;
        }

        // Adds a reference to a borrowed object.
        static auto borrow(PyObject* object) -> PyObjectRef
        {
            PyObjectRef ref;
            if (object && python_alive())
            {
                GilGuard gil;
                Py_INCREF(object);
                ref.m_object = object;
            }
            return ref;
        }

        PyObjectRef(const PyObjectRef& other) : m_object(other.m_object)
        {
            if (m_object && python_alive())
            {
                GilGuard gil;
                Py_INCREF(m_object);
            }
            else
            {
                m_object = nullptr;
            }
        }

        PyObjectRef(PyObjectRef&& other) noexcept : m_object(other.m_object) { other.m_object = nullptr; }

        auto operator=(PyObjectRef other) noexcept -> PyObjectRef&
        {
            std::swap(m_object, other.m_object);
            return *this;
        }

        ~PyObjectRef() { reset(); }

        auto reset() -> void
        {
            if (!m_object) return;
            if (python_alive())
            {
                GilGuard gil;
                Py_DECREF(m_object);
            }
            m_object = nullptr;
        }

        // The caller must hold the GIL while using the returned pointer.
        auto get() const -> PyObject* { return m_object; }
        explicit operator bool() const { return m_object != nullptr; }

    private:
        PyObject* m_object{nullptr};
    };
}
