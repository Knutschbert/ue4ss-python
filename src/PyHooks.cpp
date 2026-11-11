#include "PyHooks.hpp"

#include <map>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <deque>
#include <functional>
#include <thread>

#include <pyue/errors.hpp>
#include <pyue/log_limiter.hpp>
#include <pyue/delegate_bindings.hpp>

#include "GameThreadExecutor.hpp"
#include "Gil.hpp"
#include "DelegateApi.hpp"
#include "HookApi.hpp"
#include "ObjectApi.hpp"
#include "PyProperties.hpp"
#include "SlowCallback.hpp"
#include "PyStruct.hpp"
#include "PyText.hpp"
#include "PyUObject.hpp"
#include "UnrealBridge.hpp"

namespace pyue
{
    namespace
    {
        // Registered hooks. Callbacks hold a shared_ptr to their record, so the Python callables live as long as the engine
        // may still call them, even after unregister_hook removed the record from this map.
        struct HookRecord
        {
            objects::ObjectRef function{};
            std::wstring return_name{}; // name of the result parameter (see result_parameter); empty if the function returns nothing
            bool result_in_params{false}; // the result is an out parameter (Blueprint function), not the engine's return slot
            hooks::HookIds ids{};
            PyObjectRef pre{};
            PyObjectRef post{};
            bool has_instance{false};      // register_hook(instance=obj): only calls on that object reach the callbacks
            objects::ObjectRef instance{};
        };

        // ue4ss.NULL: returned by a post-hook to override an object result with a null reference (None means "keep").
        PyObject* s_null_sentinel = nullptr;
        PyTypeObject* s_null_type = nullptr;

        PyObject* null_repr(PyObject*)
        {
            return PyUnicode_FromString("ue4ss.NULL");
        }

        int null_bool(PyObject*)
        {
            return 0;
        }

        PyType_Slot s_null_slots[] = {
                {Py_tp_doc, const_cast<char*>("The type of ue4ss.NULL.")},
                {Py_tp_repr, reinterpret_cast<void*>(null_repr)},
                {Py_nb_bool, reinterpret_cast<void*>(null_bool)},
                {0, nullptr},
        };

        PyType_Spec s_null_spec = {"ue4ss.NullType", static_cast<int>(sizeof(PyObject)), 0, Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION, s_null_slots};

        // ue4ss.CALL_ORIGINAL: returned by a replacement to let the original function run.
        PyObject* s_call_original_sentinel = nullptr;
        PyTypeObject* s_call_original_type = nullptr;

        PyObject* call_original_repr(PyObject*)
        {
            return PyUnicode_FromString("ue4ss.CALL_ORIGINAL");
        }

        PyType_Slot s_call_original_slots[] = {
                {Py_tp_doc, const_cast<char*>("The type of ue4ss.CALL_ORIGINAL.")},
                {Py_tp_repr, reinterpret_cast<void*>(call_original_repr)},
                {0, nullptr},
        };

        PyType_Spec s_call_original_spec = {"ue4ss.CallOriginalType", static_cast<int>(sizeof(PyObject)), 0, Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION, s_call_original_slots};

        struct ReplacementRecord
        {
            int id{0};
            std::string name{};
            std::wstring return_name{};
            bool result_in_params{false}; // the result is an out parameter (Blueprint function), not the return slot
            PyObjectRef callback{};
        };

        struct ResultParameter
        {
            std::wstring name{};
            bool is_return{false};
        };

        // The parameter that carries a function's result: its return parameter or, if it has none, its only pure output.
        // Blueprint functions return through output pins, which are ordinary out parameters, so without this rule a post-hook
        // would see None for them.
        auto result_parameter(const std::vector<objects::api::ParamInfo>& params) -> ResultParameter
        {
            ResultParameter found{};
            int outputs = 0;
            for (const auto& param : params)
            {
                if (param.is_return) return ResultParameter{param.name, true};
                if (param.is_out && !param.is_reference)
                {
                    ++outputs;
                    found.name = param.name;
                }
            }
            if (outputs != 1) found.name.clear();
            return found;
        }

        auto narrow_name(NameId id) -> std::string
        {
            const std::wstring wide = bridge::name_to_string(id);
            std::string text;
            for (const wchar_t c : wide) text.push_back(c < 128 ? static_cast<char>(c) : '?');
            return text;
        }

        auto describe_hook(const void* subject) -> std::string
        {
            return narrow_name(objects::api::fname(static_cast<const HookRecord*>(subject)->function));
        }

        auto describe_replacement(const void* subject) -> std::string
        {
            return static_cast<const ReplacementRecord*>(subject)->name;
        }

        std::mutex s_mutex;
        std::map<int, std::shared_ptr<HookRecord>> s_records; // keyed by the pre id
        std::map<int, std::shared_ptr<ReplacementRecord>> s_replacements;

        LogLimiter s_log_limiter(3);
        constexpr int max_nesting = 8;
        thread_local int t_nesting = 0;

        struct NestingScope
        {
            NestingScope() { ++t_nesting; }
            ~NestingScope() { --t_nesting; }
        };

        // Writes to sys.stderr (so to the UE4SS log), rate limited per key. The caller does not hold the GIL.
        auto log_limited(const char* key, const char* message) -> void
        {
            const auto decision = s_log_limiter.should_log(key);
            if (decision == LogLimiter::Decision::Mute || !python_alive()) return;
            GilGuard gil;
            PySys_WriteStderr("%s%s\n", message, decision == LogLimiter::Decision::LogAndMute ? " (further occurrences are not logged)" : "");
        }

        // Runs one Python hook callback for one engine call. Called wherever the engine runs the hooked function.
        auto run_python_callback(const HookRecord& record, bool post, const hooks::HookCall& call) -> void
        {
            const PyObjectRef& callback = post ? record.post : record.pre;
            if (!callback || !python_alive()) return;

            // Python code and UE objects may only be touched on the game thread. A hook that fires elsewhere (an async
            // loading thread, say) is skipped: a pre-hook cannot wait for the game thread to answer.
            if (!game_thread_executor::on_game_thread())
            {
                log_limited("hook-off-thread", "ue4ss: a hooked function ran off the game thread; its Python hook was skipped");
                return;
            }
            if (t_nesting >= max_nesting)
            {
                log_limited("hook-nesting", "ue4ss: Python hooks are nested more than 8 deep (a hook calling the function it hooks?); this call was skipped");
                return;
            }

            GilGuard gil;
            NestingScope nesting;
            slow::Scope timing(post ? "post-hook" : "pre-hook", &describe_hook, &record);

            PyObject* context = nullptr;
            PyObject* params = nullptr;
            PyObject* result = nullptr;
            PyObject* result_value = nullptr;
            try
            {
                context = make_py_object(call.context); // None for a null context
                if (!context) throw PythonErrorSet{};
                params = make_py_struct(call.params);
                if (!params) throw PythonErrorSet{};
                if (post)
                {
                    // post(context, params, result): the function's return value (None if it returns nothing)
                    result_value = (call.has_result || record.result_in_params) && !record.return_name.empty()
                        ? property_value_as_python(record.result_in_params ? call.params : call.result, record.return_name)
                        : Py_NewRef(Py_None);
                    if (!result_value) throw PythonErrorSet{};
                    result = PyObject_CallFunctionObjArgs(callback.get(), context, params, result_value, nullptr);
                }
                else
                {
                    result = PyObject_CallFunctionObjArgs(callback.get(), context, params, nullptr);
                }
            }
            catch (...)
            {
                set_python_error(classify_current_exception());
            }
            Py_XDECREF(context);
            Py_XDECREF(params);
            Py_XDECREF(result_value);

            if (result)
            {
                // What the callback returned: only a post-hook can change the result, by returning the new value (None keeps the
                // original; ue4ss.NULL sets a null object reference).
                if (result != Py_None)
                {
                    if (!post)
                    {
                        log_limited("hook-pre-return", "ue4ss: a pre-hook returned a value; only post-hooks can change the result, so it was ignored");
                    }
                    else if (record.return_name.empty() || !(call.has_result || record.result_in_params))
                    {
                        log_limited("hook-no-result-slot", "ue4ss: a post-hook returned a value but this function has no result to override; it was ignored");
                    }
                    else
                    {
                        try
                        {
                            property_assign(record.result_in_params ? call.params : call.result, record.return_name, result == s_null_sentinel ? Py_None : result);
                        }
                        catch (...)
                        {
                            // A bad replacement (wrong type, out of range) is reported; the original result stays.
                            set_python_error(classify_current_exception());
                            PyErr_Print();
                        }
                    }
                }
                Py_DECREF(result);
                return;
            }
            // An exception in a hook is reported and swallowed; it must never reach the engine. sys.exit() must not
            // take the game down either.
            if (PyErr_ExceptionMatches(PyExc_SystemExit))
            {
                PyErr_Clear();
                PySys_WriteStderr("sys.exit() inside a hook is ignored\n");
            }
            else
            {
                PyErr_Print();
            }
        }

        auto make_trampoline(std::shared_ptr<HookRecord> record, bool post) -> hooks::HookCallback
        {
            return [record = std::move(record), post](const hooks::HookCall& call) {
                if (record->has_instance && !(call.context.handle == record->instance.handle)) return;   // another object: cheap exit, no Python
                run_python_callback(*record, post, call);
            };
        }

        auto as_callback(PyObject* object, const char* what) -> PyObjectRef
        {
            if (object == Py_None) return PyObjectRef{};
            if (!PyCallable_Check(object))
            {
                PyErr_Format(PyExc_TypeError, "%s must be callable or None, not %.200s", what, Py_TYPE(object)->tp_name);
                throw PythonErrorSet{};
            }
            return PyObjectRef::borrow(object);
        }
    }

    auto init_hook_types(PyObject* module) -> bool
    {
        PyObject* original_type = PyType_FromSpec(&s_call_original_spec);
        if (!original_type) return false;
        s_call_original_type = reinterpret_cast<PyTypeObject*>(original_type);
        s_call_original_sentinel = s_call_original_type->tp_alloc(s_call_original_type, 0);
        if (!s_call_original_sentinel) return false;
        if (PyModule_AddObjectRef(module, "CallOriginalType", original_type) != 0) return false;
        if (PyModule_AddObjectRef(module, "CALL_ORIGINAL", s_call_original_sentinel) != 0) return false;

        PyObject* type = PyType_FromSpec(&s_null_spec);
        if (!type) return false;
        s_null_type = reinterpret_cast<PyTypeObject*>(type);
        s_null_sentinel = s_null_type->tp_alloc(s_null_type, 0); // lives for the process
        if (!s_null_sentinel) return false;
        if (PyModule_AddObjectRef(module, "NullType", type) != 0) return false;
        return PyModule_AddObjectRef(module, "NULL", s_null_sentinel) == 0;
    }

    // --- event dispatcher binding (bind_event / unbind_event) -------------------------------------------------------------
    // A Python callback bound to a multicast delegate property. See DelegateApi.hpp for the mechanism: we add an
    // FScriptDelegate entry to the owning object's own InvocationList (so a broadcast runs the signature UFunction on
    // exactly that object) and dispatch through the global ProcessEvent/local-script callbacks, multiplexed by the
    // broadcasting object via the core DelegateBindings table.

    struct EventRecord
    {
        int id{0};
        objects::ObjectRef object{};      // the object whose delegate we bound (for the InvocationList entry)
        objects::ObjectRef signature{};   // the __DelegateSignature UFunction (we dispatch on it)
        std::wstring name{};              // the delegate property name (for add/remove entry)
        PyObjectRef callback{};
        bool async{false};
    };

        std::mutex s_events_mutex;
        std::map<int, std::shared_ptr<EventRecord>> s_events; // by binding id

        class AsyncEventWorker
        {
        public:
            auto post(std::function<void()> task) -> void
            {
                {
                    std::lock_guard lock{m_mutex};
                    if (!m_started)
                    {
                        m_started = true;
                        std::thread([this] { run(); }).detach();
                    }
                    m_tasks.push_back(std::move(task));
                }
                m_ready.notify_one();
            }

        private:
            auto run() -> void
            {
                for (;;)
                {
                    std::function<void()> task;
                    {
                        std::unique_lock lock{m_mutex};
                        m_ready.wait(lock, [this] { return !m_tasks.empty(); });
                        task = std::move(m_tasks.front());
                        m_tasks.pop_front();
                    }
                    try
                    {
                        task();
                    }
                    catch (...)
                    {
                    }
                }
            }

            std::mutex m_mutex;
            std::condition_variable m_ready;
            std::deque<std::function<void()>> m_tasks;
            bool m_started{false};
        };

        auto async_event_worker() -> AsyncEventWorker&
        {
            static auto* worker = new AsyncEventWorker();
            return *worker;
        }

        auto event_binding_active(int id) -> bool
        {
            std::lock_guard lock{s_events_mutex};
            return s_events.find(id) != s_events.end();
        }

        struct AsyncEventPayload
        {
            int binding_id{0};
            PyObjectRef callback{};
            PyObjectRef context{};
            PyObjectRef params{};
        };

    // The callback DelegateApi invokes for each active binding id on a broadcast. `params` is borrowed and dies when
    // this returns. Mirrors run_python_callback's guards.
    auto dispatch_event(const hooks::HookCall& call, int binding_id) -> void
    {
        if (!python_alive()) return;
        std::shared_ptr<EventRecord> record;
        {
            std::lock_guard lock{s_events_mutex};
            const auto it = s_events.find(binding_id);
            if (it != s_events.end()) record = it->second;
        }
        if (!record || !record->callback) return;
        if (!game_thread_executor::on_game_thread())
        {
            log_limited("event-off-thread", "ue4ss: a delegate broadcast ran off the game thread; its bound callbacks were skipped");
            return;
        }
        if (t_nesting >= max_nesting)
        {
            log_limited("event-nesting", "ue4ss: delegate broadcasts are nested more than 8 deep; this broadcast was skipped");
            return;
        }

        GilGuard gil;
        NestingScope nesting;
        if (record->async)
        {
            PyObject* context = make_py_object(call.context);
            PyObject* params = nullptr;
            try
            {
                params = make_py_struct(objects::properties::duplicate_struct(call.params));
                if (!context || !params) throw PythonErrorSet{};
            }
            catch (...)
            {
                Py_XDECREF(context);
                Py_XDECREF(params);
                set_python_error(classify_current_exception());
                PyErr_Print();
                return;
            }
            auto payload = std::make_shared<AsyncEventPayload>();
            payload->binding_id = binding_id;
            payload->callback = record->callback;
            payload->context = PyObjectRef::steal(context);
            payload->params = PyObjectRef::steal(params);
            async_event_worker().post([payload] {
                if (!event_binding_active(payload->binding_id) || !python_alive()) return;
                GilGuard worker_gil;
                PyObject* result = PyObject_CallFunctionObjArgs(payload->callback.get(), payload->context.get(), payload->params.get(), nullptr);
                if (result)
                {
                    Py_DECREF(result);
                    return;
                }
                if (PyErr_ExceptionMatches(PyExc_SystemExit))
                {
                    PyErr_Clear();
                    PySys_WriteStderr("sys.exit() inside an async delegate callback is ignored\n");
                }
                else
                {
                    PyErr_Print();
                }
            });
            return;
        }
        slow::Scope timing("bind_event");

        PyObject* context = make_py_object(call.context);
        PyObject* params = context ? make_py_struct(call.params) : nullptr;
        if (!context || !params)
        {
            Py_XDECREF(context);
            Py_XDECREF(params);
            set_python_error(classify_current_exception());
            PyErr_Print();
            return;
        }
        PyObject* result = PyObject_CallFunctionObjArgs(record->callback.get(), context, params, nullptr);
        if (!result)
        {
            if (PyErr_ExceptionMatches(PyExc_SystemExit))
            {
                PyErr_Clear();
                PySys_WriteStderr("sys.exit() inside a bound delegate callback is ignored\n");
            }
            else
            {
                PyErr_Print();
            }
        }
        Py_XDECREF(result);
        Py_DECREF(context);
        Py_DECREF(params);
    }

    PyObject* py_bind_event(PyObject*, PyObject* args, PyObject* kwargs)
    {
        return guard_object([&]() -> PyObject* {
            static const char* keywords[] = {"object", "name", "callback", "async_", nullptr};
            PyObject* object_object = nullptr;
            PyObject* name_object = nullptr;
            PyObject* callback = nullptr;
            int async = 0;
            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOO|p:bind_event", const_cast<char**>(keywords), &object_object, &name_object, &callback, &async))
            {
                throw PythonErrorSet{};
            }
            if (!PyCallable_Check(callback))
            {
                PyErr_Format(PyExc_TypeError, "callback must be callable, not %.200s", Py_TYPE(callback)->tp_name);
                throw PythonErrorSet{};
            }
            std::wstring name;
            if (!wide_from_py(name_object, name)) throw PythonErrorSet{};

            objects::ObjectRef object;
            if (!object_ref_from_py(object_object, object) || object.handle.index < 0)
            {
                PyErr_Format(PyExc_TypeError, "object must be a UObject, not %.200s", Py_TYPE(object_object)->tp_name);
                throw PythonErrorSet{};
            }

            // Resolve the multicast delegate property and its signature function (game thread inside).
            const delegates::Bound bound = delegates::resolve(object, name);

            // Make the broadcast dispatcher deliver to our Python callbacks (idempotent; set before any broadcast).
            static bool dispatcher_set = false;
            if (!dispatcher_set)
            {
                delegates::set_broadcast_dispatcher([](const hooks::HookCall& call, int binding_id) { dispatch_event(call, binding_id); });
                dispatcher_set = true;
            }

            pyue::DelegateBindings::Added added;
            {
                std::lock_guard lock{s_events_mutex};
                added = delegates::bindings().add(bound.signature_key, bound.object_key);
            }
            try
            {
                if (added.needs_hook)
                {
                    // First binding of this signature anywhere: make the global broadcast callbacks dispatch it.
                    const bool script = objects::api::kind_of(bound.signature) == objects::api::ObjectKind::Function &&
                                        (objects::api::function_flags(bound.signature) & static_cast<std::uint32_t>(0x00000400u)) == 0; // FUNC_Native
                    delegates::note_signature(bound.signature, script);
                }
                if (added.needs_entry)
                {
                    delegates::add_entry(object, bound, name);
                }

                auto record = std::make_shared<EventRecord>();
                record->id = added.id;
                record->object = object;
                record->signature = bound.signature;
                record->name = name;
                record->callback = PyObjectRef::borrow(callback);
                record->async = async != 0;
                {
                    std::lock_guard lock{s_events_mutex};
                    s_events[added.id] = record;
                }
                return PyLong_FromLong(added.id);
            }
            catch (...)
            {
                pyue::DelegateBindings::Removed removed;
                const bool removed_any = delegates::bindings().remove(added.id, &removed);
                if (removed_any && removed.last_for_pair && added.needs_entry)
                {
                    try
                    {
                        delegates::remove_entry(object, bound, name);
                    }
                    catch (...)
                    {
                    }
                }
                if (removed_any && removed.last_for_signature && added.needs_hook)
                {
                    try
                    {
                        delegates::forget_signature(bound.signature);
                    }
                    catch (...)
                    {
                    }
                }
                throw;
            }
        });
    }

    PyObject* py_unbind_event(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            game_thread_executor::require_game_thread();
            int id = 0;
            if (!PyArg_ParseTuple(args, "i:unbind_event", &id)) throw PythonErrorSet{};

            std::shared_ptr<EventRecord> record;
            pyue::DelegateBindings::Removed removed;
            bool removed_any;
            {
                std::lock_guard lock{s_events_mutex};
                const auto it = s_events.find(id);
                if (it == s_events.end())
                {
                    PyErr_SetString(PyExc_ValueError, "no event binding with that id (already unbound?)");
                    throw PythonErrorSet{};
                }
                record = it->second;
                s_events.erase(it);
                removed_any = delegates::bindings().remove(id, &removed);
            }

            // Tear down the engine side only when our registrations are the last for the pair / signature.
            if (removed_any && removed.last_for_pair)
            {
                try
                {
                    delegates::remove_entry(record->object, delegates::Bound{record->signature, 0, 0}, record->name);
                }
                catch (...)
                {
                    // The object or property is gone; the entry is gone with the object. Nothing to do.
                }
            }
            if (removed_any && removed.last_for_signature)
            {
                try
                {
                    delegates::forget_signature(record->signature);
                }
                catch (...)
                {
                    // Shutting down, or the function is gone; the global callbacks find nothing to do once the table is empty.
                }
            }
            Py_RETURN_NONE;
        });
    }

    PyObject* py_register_hook(PyObject*, PyObject* args, PyObject* kwargs)
    {
        return guard_object([&]() -> PyObject* {
            static const char* keywords[] = {"path", "pre", "post", "instance", nullptr};
            PyObject* path_object = nullptr;
            PyObject* pre_object = Py_None;
            PyObject* post_object = Py_None;
            PyObject* instance_object = Py_None;
            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOO:register_hook", const_cast<char**>(keywords), &path_object, &pre_object, &post_object, &instance_object))
            {
                throw PythonErrorSet{};
            }

            std::wstring path;
            if (!wide_from_py(path_object, path)) throw PythonErrorSet{};
            auto record = std::make_shared<HookRecord>();
            record->pre = as_callback(pre_object, "pre");
            record->post = as_callback(post_object, "post");
            if (!record->pre && !record->post)
            {
                PyErr_SetString(PyExc_TypeError, "register_hook() needs a pre callback, a post callback, or both");
                throw PythonErrorSet{};
            }

            if (instance_object != Py_None)
            {
                if (!object_ref_from_py(instance_object, record->instance) || record->instance.handle.index < 0)
                {
                    PyErr_Format(PyExc_TypeError, "instance must be a UObject or None, not %.200s", Py_TYPE(instance_object)->tp_name);
                    throw PythonErrorSet{};
                }
                record->has_instance = true;
            }

            record->function = objects::api::find_by_path(path);
            if (record->function.handle.index < 0 || objects::api::kind_of(record->function) != objects::api::ObjectKind::Function)
            {
                throw UEError("no function with that path (use the form /Script/Engine.Actor:ReceiveBeginPlay)");
            }

            {
                const ResultParameter result_slot = result_parameter(objects::api::function_params(record->function));
                record->return_name = result_slot.name;
                record->result_in_params = !result_slot.name.empty() && !result_slot.is_return;
            }

            record->ids = hooks::register_hook(record->function, make_trampoline(record, false), make_trampoline(record, true));
            {
                std::lock_guard lock{s_mutex};
                s_records[record->ids.pre] = record;
            }
            return Py_BuildValue("(ii)", record->ids.pre, record->ids.post);
        });
    }

    PyObject* py_unregister_hook(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            game_thread_executor::require_game_thread(); // before anything is forgotten: a refused call must change nothing
            int pre_id = 0;
            int post_id = 0;
            if (!PyArg_ParseTuple(args, "(ii):unregister_hook", &pre_id, &post_id)) // the tuple register_hook returned...
            {
                PyErr_Clear();
                if (!PyArg_ParseTuple(args, "ii:unregister_hook", &pre_id, &post_id)) throw PythonErrorSet{}; // ...or the two ids
            }
            std::shared_ptr<HookRecord> record;
            {
                std::lock_guard lock{s_mutex};
                const auto it = s_records.find(pre_id);
                if (it == s_records.end() || it->second->ids.post != post_id)
                {
                    PyErr_SetString(PyExc_ValueError, "no hook with those ids (already unregistered?)");
                    throw PythonErrorSet{};
                }
                record = it->second;
                s_records.erase(it);
            }
            hooks::unregister_hook(record->function, record->ids);
            Py_RETURN_NONE;
        });
    }

    PyObject* py_register_replacement(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            PyObject* path_object = nullptr;
            PyObject* callback = nullptr;
            if (!PyArg_ParseTuple(args, "OO:register_replacement", &path_object, &callback)) throw PythonErrorSet{};
            if (!PyCallable_Check(callback))
            {
                PyErr_Format(PyExc_TypeError, "callback must be callable, not %.200s", Py_TYPE(callback)->tp_name);
                throw PythonErrorSet{};
            }
            std::wstring path;
            if (!wide_from_py(path_object, path)) throw PythonErrorSet{};
            const objects::ObjectRef function = objects::api::find_by_path(path);
            if (function.handle.index < 0 || objects::api::kind_of(function) != objects::api::ObjectKind::Function)
            {
                throw UEError("no function with that path (use the form /Script/Engine.Actor:ReceiveBeginPlay)");
            }
            auto record = std::make_shared<ReplacementRecord>();
            record->callback = PyObjectRef::borrow(callback);
            record->name = narrow_name(objects::api::fname(function));
            {
                const ResultParameter result_slot = result_parameter(objects::api::function_params(function));
                record->return_name = result_slot.name;
                record->result_in_params = !result_slot.name.empty() && !result_slot.is_return;
            }
            record->id = hooks::register_replacement(function, [record](const hooks::HookCall& call) -> bool {
                if (!python_alive()) return false;
                GilGuard gil;
                slow::Scope timing("replacement", &describe_replacement, record.get());
                PyObject* context = make_py_object(call.context);
                PyObject* params = context ? make_py_struct(call.params) : nullptr;
                PyObject* result = params ? PyObject_CallFunctionObjArgs(record->callback.get(), context, params, nullptr) : nullptr;
                Py_XDECREF(context);
                Py_XDECREF(params);
                if (!result)
                {
                    // An exception in a replacement is reported; the original runs, as if it had returned CALL_ORIGINAL.
                    if (PyErr_ExceptionMatches(PyExc_SystemExit))
                    {
                        PyErr_Clear();
                        PySys_WriteStderr("sys.exit() inside a replacement is ignored\n");
                    }
                    else
                    {
                        PyErr_Print();
                    }
                    return false;
                }
                bool skip = true;
                if (result == s_call_original_sentinel)
                {
                    skip = false;
                }
                else if (result != Py_None)
                {
                    if (record->return_name.empty())
                    {
                        PySys_WriteStderr("ue4ss: a replacement returned a value but the function has no result; the value was ignored\n");
                    }
                    else
                    {
                        try
                        {
                            // ProcessEvent: the return value lives in the parameter block. Script VM: it goes to RESULT_DECL (has_result).
                            property_assign(call.has_result && !record->result_in_params ? call.result : call.params, record->return_name, result == s_null_sentinel ? Py_None : result);
                        }
                        catch (...)
                        {
                            // A bad result (wrong type, out of range): report it and let the original run rather than return garbage.
                            set_python_error(classify_current_exception());
                            PyErr_Print();
                            skip = false;
                        }
                    }
                }
                Py_DECREF(result);
                return skip;
            });
            {
                std::lock_guard lock{s_mutex};
                s_replacements[record->id] = record;
            }
            return PyLong_FromLong(record->id);
        });
    }

    PyObject* py_unregister_replacement(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            int id = 0;
            if (!PyArg_ParseTuple(args, "i:unregister_replacement", &id)) throw PythonErrorSet{};
            if (!hooks::unregister_replacement(id))
            {
                PyErr_SetString(PyExc_ValueError, "no replacement with that id (already unregistered?)");
                throw PythonErrorSet{};
            }
            std::lock_guard lock{s_mutex};
            s_replacements.erase(id);
            Py_RETURN_NONE;
        });
    }

    auto unregister_all_hooks() -> void
    {
        hooks::unregister_all_replacements();
        {
            std::lock_guard lock{s_mutex};
            s_replacements.clear();
        }
        // Event bindings: drop the Python callbacks and, best effort, the engine entries and signature hooks. The
        // global broadcast callbacks then find nothing to do once the table is empty (and forget_signature/auth the rest).
        {
            std::lock_guard lock{s_events_mutex};
            s_events.clear();
        }
        delegates::reset_broadcast();
        std::map<int, std::shared_ptr<HookRecord>> records;
        {
            std::lock_guard lock{s_mutex};
            records.swap(s_records);
        }
        for (auto& [id, record] : records)
        {
            try
            {
                hooks::unregister_hook(record->function, record->ids);
            }
            catch (...)
            {
                // Not on the game thread, or the function is gone: the hook stays installed but inert once Python is down.
            }
        }
    }
}
