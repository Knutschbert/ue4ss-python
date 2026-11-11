#pragma once

#include <string>

#include "PythonApi.hpp"

// str <-> UTF-16 conversions. On Windows wchar_t is UTF-16, which is what Unreal's TCHAR uses, and
// Python's wide-character API converts to and from it correctly, including surrogate pairs.
static_assert(sizeof(wchar_t) == 2, "the bindings only support Windows, where wchar_t is UTF-16");

namespace pyue
{
    // New reference, or nullptr with a Python exception set.
    inline auto py_from_wide(const std::wstring& text) -> PyObject*
    {
        return PyUnicode_FromWideChar(text.data(), static_cast<Py_ssize_t>(text.size()));
    }

    // False with a Python exception set if `object` is not a str.
    inline auto wide_from_py(PyObject* object, std::wstring& out) -> bool
    {
        if (!PyUnicode_Check(object))
        {
            PyErr_Format(PyExc_TypeError, "expected str, not %.200s", Py_TYPE(object)->tp_name);
            return false;
        }
        Py_ssize_t size = 0;
        wchar_t* buffer = PyUnicode_AsWideCharString(object, &size);
        if (!buffer) return false;
        out.assign(buffer, static_cast<size_t>(size));
        PyMem_Free(buffer);
        return true;
    }
}
