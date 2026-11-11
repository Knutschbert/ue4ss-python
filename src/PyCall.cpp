#include "PyCall.hpp"

#include <string>

#include <pyue/errors.hpp>
#include <pyue/identifier.hpp>

#include "Gil.hpp"
#include "PyProperties.hpp"
#include "PyStruct.hpp"
#include "PyText.hpp"
#include "UnrealBridge.hpp"

namespace pyue
{
    namespace properties = objects::properties;

    namespace
    {
        auto narrow_ascii(const std::wstring& text) -> std::string
        {
            std::string result;
            for (const wchar_t c : text) result.push_back(c < 128 ? static_cast<char>(c) : '?');
            return result;
        }

        auto function_name(const objects::ObjectRef& function) -> std::string
        {
            return narrow_ascii(bridge::name_to_string(objects::api::fname(function)));
        }

        // --- ue4ss.BoundFunction ----------------------------------------------------------------

        struct BoundFunctionObject
        {
            PyObject_HEAD
            objects::ObjectRef object;
            objects::ObjectRef function;
        };

        PyTypeObject* s_bound_function_type = nullptr;

        PyObject* bound_function_call(PyObject* self, PyObject* args, PyObject* kwargs)
        {
            auto* bound = reinterpret_cast<BoundFunctionObject*>(self);
            return guard_object([&]() -> PyObject* { return call_function_from_python(bound->object, bound->function, args, kwargs); });
        }

        // repr() must never raise or crash.
        PyObject* bound_function_repr(PyObject* self)
        {
            auto* bound = reinterpret_cast<BoundFunctionObject*>(self);
            std::wstring text;
            try
            {
                text = L"<BoundFunction '" + bridge::name_to_string(objects::api::fname(bound->function)) + L"'>";
            }
            catch (const InvalidObjectError&)
            {
                text = L"<BoundFunction (destroyed)>";
            }
            catch (...)
            {
                text = L"<BoundFunction (not readable here)>";
            }
            return py_from_wide(text);
        }

        PyObject* bound_function_is_valid(PyObject* self, PyObject*)
        {
            auto* bound = reinterpret_cast<BoundFunctionObject*>(self);
            if (objects::is_valid(bound->object) && objects::is_valid(bound->function)) Py_RETURN_TRUE;
            Py_RETURN_FALSE;
        }

        PyMethodDef s_methods[] = {
                {"is_valid", bound_function_is_valid, METH_NOARGS, "is_valid() -> bool: False once the object or the function was destroyed. Never raises."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyType_Slot s_slots[] = {
                {Py_tp_doc, const_cast<char*>("A function of an object, ready to call. Calling it runs the function in the game and returns its result.")},
                {Py_tp_call, reinterpret_cast<void*>(bound_function_call)},
                {Py_tp_repr, reinterpret_cast<void*>(bound_function_repr)},
                {Py_tp_methods, s_methods},
                {0, nullptr},
        };

        // Created by attribute lookup on an object, never by construction.
        PyType_Spec s_spec = {
                "ue4ss.BoundFunction",
                static_cast<int>(sizeof(BoundFunctionObject)),
                0,
                Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
                s_slots,
        };
    }

    auto init_call_types(PyObject* module) -> bool
    {
        PyObject* type = PyType_FromSpec(&s_spec);
        if (!type) return false;
        s_bound_function_type = reinterpret_cast<PyTypeObject*>(type); // keeps the creation reference for the process lifetime
        return PyModule_AddObjectRef(module, "BoundFunction", type) == 0;
    }

    auto make_bound_function(const objects::ObjectRef& object, const objects::ObjectRef& function) -> PyObject*
    {
        if (!s_bound_function_type)
        {
            PyErr_SetString(PyExc_RuntimeError, "ue4ss.BoundFunction is not initialised");
            return nullptr;
        }
        PyObject* created = s_bound_function_type->tp_alloc(s_bound_function_type, 0);
        if (!created) return nullptr;
        auto* bound = reinterpret_cast<BoundFunctionObject*>(created);
        bound->object = object;
        bound->function = function;
        return created;
    }

    auto build_param_block(const objects::ObjectRef& function, PyObject* args, PyObject* kwargs) -> ParamBlock
    {
        ParamBlock result;
        result.params = objects::api::function_params(function);
        for (const auto& param : result.params)
        {
            result.specs.push_back(ParamSpec{narrow_ascii(param.name), param.is_out, param.is_return, param.is_const, param.is_reference});
        }

        std::vector<std::string> keyword_names;
        std::vector<PyObject*> keyword_values;
        if (kwargs)
        {
            PyObject* key = nullptr;
            PyObject* value = nullptr;
            Py_ssize_t position = 0;
            while (PyDict_Next(kwargs, &position, &key, &value))
            {
                std::wstring wide;
                if (!wide_from_py(key, wide)) throw PythonErrorSet{};
                keyword_names.push_back(narrow_ascii(wide));
                keyword_values.push_back(value);
            }
        }

        // A keyword may be the identifier form of a parameter whose engine name is not an identifier ("Sun_height" for "Sun height"), as the typings
        // declare it; used when no parameter has exactly that name and one has that form.
        for (std::string& keyword : keyword_names)
        {
            const std::string_view wanted(keyword);
            bool exact = false;
            for (const ParamSpec& spec : result.specs) exact = exact || spec.name == wanted;
            if (exact) continue;
            const ParamSpec* alias = nullptr;
            int matches = 0;
            for (const ParamSpec& spec : result.specs)
            {
                if (pyue::is_identifier_alias(wanted, std::string_view(spec.name)))
                {
                    alias = &spec;
                    ++matches;
                }
            }
            if (matches == 1) keyword = alias->name;
        }

        const BindResult bound = bind_arguments(result.specs, static_cast<std::size_t>(PyTuple_GET_SIZE(args)), keyword_names);
        if (!bound.ok)
        {
            PyErr_Format(PyExc_TypeError, "%s() %s", function_name(function).c_str(), bound.error.c_str());
            throw PythonErrorSet{};
        }

        result.block = properties::make_param_block(function);

        PyObject* values = PyDict_New();
        if (!values) throw PythonErrorSet{};
        try
        {
            for (const Assignment& assignment : bound.assignments)
            {
                PyObject* value = assignment.source == Assignment::Source::Positional ? PyTuple_GET_ITEM(args, static_cast<Py_ssize_t>(assignment.index))
                                                                                        : keyword_values[assignment.index];
                PyObject* key = py_from_wide(result.params[assignment.param].name);
                if (!key || PyDict_SetItem(values, key, value) != 0)
                {
                    Py_XDECREF(key);
                    throw PythonErrorSet{};
                }
                Py_DECREF(key);
            }
            properties_assign_fields(result.block, values); // the usual strict conversions; raises on a bad value
        }
        catch (...)
        {
            Py_DECREF(values);
            throw;
        }
        Py_DECREF(values);
        return result;
    }

    auto call_function_from_python(const objects::ObjectRef& object, const objects::ObjectRef& function, PyObject* args, PyObject* kwargs) -> PyObject*
    {
        // Validate and convert everything first: a bad argument must fail before the game is touched.
        const ParamBlock prepared = build_param_block(function, args, kwargs);
        const objects::ObjectRef context = objects::api::call_context(object, function);

        {
            // The engine may run hooks, other threads may need the GIL, and the callee may block: do not hold it.
            GilRelease unlocked;
            properties::call_function(context, function, prepared.block);
        }

        const std::vector<std::size_t> plan = plan_results(prepared.specs);
        if (plan.empty()) Py_RETURN_NONE;

        PyObject* tuple = plan.size() > 1 ? PyTuple_New(static_cast<Py_ssize_t>(plan.size())) : nullptr;
        if (plan.size() > 1 && !tuple) throw PythonErrorSet{};

        for (std::size_t i = 0; i < plan.size(); ++i)
        {
            PyObject* value = property_value_as_python(prepared.block, prepared.params[plan[i]].name);
            if (!value)
            {
                Py_XDECREF(tuple);
                throw PythonErrorSet{};
            }
            if (!tuple) return value;
            PyTuple_SET_ITEM(tuple, static_cast<Py_ssize_t>(i), value); // steals the reference
        }
        return tuple;
    }
}
