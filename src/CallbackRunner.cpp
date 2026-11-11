#include "CallbackRunner.hpp"

#include "SlowCallback.hpp"

namespace pyue
{
    auto call_logged(PyObject* callable) -> bool
    {
        PyObject* result = nullptr;
        {
            slow::Scope timing("game-thread task");
            result = PyObject_CallNoArgs(callable);
        }
        if (result)
        {
            Py_DECREF(result);
            return true;
        }

        if (PyErr_ExceptionMatches(PyExc_SystemExit))
        {
            // PyErr_Print() would call exit() for SystemExit and take the game down with it.
            PyErr_Clear();
            PySys_WriteStderr("sys.exit() inside a callback is ignored\n");
        }
        else
        {
            PyErr_Print();
        }
        return false;
    }
}
