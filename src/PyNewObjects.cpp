#include "PyNewObjects.hpp"

#include <map>
#include <memory>
#include <mutex>

#include <pyue/errors.hpp>

#include "Gil.hpp"
#include "NewObjectApi.hpp"
#include "ObjectApi.hpp"
#include "PyText.hpp"
#include "PyUObject.hpp"
#include "SlowCallback.hpp"

namespace pyue
{
    namespace
    {
        // The callable lives as long as the engine side may still call it.
        struct Record
        {
            PyObjectRef callback{};
        };

        std::mutex s_mutex;
        std::map<int, std::shared_ptr<Record>> s_records;
    }

    PyObject* py_notify_on_new_object(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            PyObject* path_object = nullptr;
            PyObject* callback = nullptr;
            if (!PyArg_ParseTuple(args, "OO:notify_on_new_object", &path_object, &callback)) throw PythonErrorSet{};
            if (!PyCallable_Check(callback))
            {
                PyErr_Format(PyExc_TypeError, "callback must be callable, not %.200s", Py_TYPE(callback)->tp_name);
                throw PythonErrorSet{};
            }
            std::wstring path;
            if (!wide_from_py(path_object, path)) throw PythonErrorSet{};

            const objects::ObjectRef cls = objects::api::find_by_path(path);
            if (cls.handle.index < 0 || objects::api::kind_of(cls) != objects::api::ObjectKind::Class)
            {
                throw UEError("no class with that path (use the form /Script/Engine.Actor)");
            }

            auto record = std::make_shared<Record>();
            record->callback = PyObjectRef::borrow(callback);
            const int id = objects::created::subscribe(cls, [record](const objects::ObjectRef& ref) {
                if (!python_alive()) return;
                GilGuard gil;
                slow::Scope timing("new-object callback");
                PyObject* object = make_py_object(ref);
                if (!object)
                {
                    PyErr_Print();
                    return;
                }
                PyObject* args_tuple = PyTuple_Pack(1, object);
                Py_DECREF(object);
                if (!args_tuple)
                {
                    PyErr_Print();
                    return;
                }
                PyObject* bound = PyObject_Call(record->callback.get(), args_tuple, nullptr);
                Py_DECREF(args_tuple);
                if (bound)
                {
                    Py_DECREF(bound);
                    return;
                }
                if (PyErr_ExceptionMatches(PyExc_SystemExit))
                {
                    PyErr_Clear();
                    PySys_WriteStderr("sys.exit() inside a new-object callback is ignored\n");
                }
                else
                {
                    PyErr_Print();
                }
            });
            {
                std::lock_guard lock{s_mutex};
                s_records[id] = record;
            }
            return PyLong_FromLong(id);
        });
    }

    PyObject* py_unregister_notify_on_new_object(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            int id = 0;
            if (!PyArg_ParseTuple(args, "i:unregister_notify_on_new_object", &id)) throw PythonErrorSet{};
            if (!objects::created::unsubscribe(id))
            {
                PyErr_SetString(PyExc_ValueError, "no subscription with that id (already unregistered?)");
                throw PythonErrorSet{};
            }
            std::lock_guard lock{s_mutex};
            s_records.erase(id);
            Py_RETURN_NONE;
        });
    }

    auto unregister_all_new_object_notifications() -> void
    {
        objects::created::unsubscribe_all();
        std::lock_guard lock{s_mutex};
        s_records.clear();
    }
}
