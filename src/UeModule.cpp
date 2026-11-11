#include "ThreadProbe.hpp"
#include "UeModule.hpp"

#include <string>
#include <string_view>

#include <chrono>

#include <pyue/version.hpp>

#include "CallbackRunner.hpp"
#include "GameThreadExecutor.hpp"
#include "Gil.hpp"
#include "ObjectApi.hpp"
#include "ObjectRegistry.hpp"
#include "PropertyApi.hpp"
#include "DelegateApi.hpp"
#include "PyProperties.hpp"
#include "PyCall.hpp"
#include "PyArray.hpp"
#include "PyFixedArray.hpp"
#include "PyHooks.hpp"
#include "SlowCallback.hpp"
#include "PyMapSet.hpp"
#include "PyRotation.hpp"
#include "PyKeys.hpp"
#include "Reload.hpp"
#include "PyNewObjects.hpp"
#include "PyStruct.hpp"
#include "PyUObject.hpp"
#include "PyFName.hpp"
#include "PyText.hpp"
#include "UnrealBridge.hpp"

namespace pyue
{
    namespace
    {
        // Created once, when the module is first imported; the interpreter lives for the whole process.
        PyObject* s_ue_error = nullptr;
        PyObject* s_invalid_object_error = nullptr;

        // Raises each kind of C++ exception so the self check can verify the mapping end to end.
        PyObject* selftest_raise(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                if (!PyUnicode_Check(arg))
                {
                    PyErr_SetString(PyExc_TypeError, "kind must be a str");
                    throw PythonErrorSet{};
                }
                const char* text = PyUnicode_AsUTF8(arg);
                if (!text) throw PythonErrorSet{};

                const std::string_view kind{text};
                if (kind == "none") Py_RETURN_NONE;
                if (kind == "ue") throw UEError("test UEError");
                if (kind == "invalid") throw InvalidObjectError("test InvalidObjectError");
                if (kind == "property") throw PropertyNotFoundError("test PropertyNotFoundError");
                if (kind == "std") throw std::runtime_error("test std::runtime_error");
                if (kind == "memory") throw std::bad_alloc();
                if (kind == "unknown") throw 42;

                PyErr_Format(PyExc_ValueError, "unknown selftest kind: %s", text);
                throw PythonErrorSet{};
            });
        }

        // --- game-thread scheduling ---------------------------------------------------------

        auto require_executor() -> void
        {
            if (game_thread_executor::method() == game_thread_executor::Method::None)
            {
                throw UEError("no game-thread hook is available (neither EngineTick nor ProcessEvent was found)");
            }
        }

        auto require_callable(PyObject* object) -> void
        {
            if (!PyCallable_Check(object))
            {
                PyErr_SetString(PyExc_TypeError, "callback must be callable");
                throw PythonErrorSet{};
            }
        }

        // The task owns a reference to the callable (valid on any thread) and takes the GIL itself.
        auto make_task(PyObject* callable) -> WorkQueue::Task
        {
            return [callback = PyObjectRef::borrow(callable)] {
                GilGuard gil;
                call_logged(callback.get());
            };
        }

        PyObject* execute_in_game_thread(PyObject*, PyObject* callable)
        {
            return guard_object([&]() -> PyObject* {
                require_callable(callable);
                require_executor();
                game_thread_executor::post(make_task(callable));
                Py_RETURN_NONE;
            });
        }

        PyObject* execute_with_delay(PyObject*, PyObject* args)
        {
            return guard_object([&]() -> PyObject* {
                double milliseconds = 0;
                PyObject* callable = nullptr;
                if (!PyArg_ParseTuple(args, "dO:execute_with_delay", &milliseconds, &callable)) throw PythonErrorSet{};
                if (!(milliseconds >= 0))
                {
                    PyErr_SetString(PyExc_ValueError, "delay must be a non-negative number of milliseconds");
                    throw PythonErrorSet{};
                }
                require_callable(callable);
                require_executor();
                game_thread_executor::post_delayed(make_task(callable),
                                                   std::chrono::milliseconds(static_cast<long long>(milliseconds)));
                Py_RETURN_NONE;
            });
        }

        // Round-trips text through an Unreal FString (self test of the FString conversion).
        PyObject* selftest_fstring(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring text;
                if (!wide_from_py(arg, text)) throw PythonErrorSet{};
                return py_from_wide(bridge::fstring_round_trip(text));
            });
        }

        // TEST ONLY: writes a deliberately bad pointer into an object-pointer field of a struct (see poison_object_pointer).
        PyObject* selftest_poison_pointer(PyObject*, PyObject* args)
        {
            return guard_object([&]() -> PyObject* {
                PyObject* target_object = nullptr;
                PyObject* name_object = nullptr;
                int mode = 0;
                if (!PyArg_ParseTuple(args, "OOi:_selftest_poison_pointer", &target_object, &name_object, &mode)) throw PythonErrorSet{};
                objects::properties::Target target;
                if (!struct_target_from_py(target_object, target))
                {
                    PyErr_SetString(PyExc_TypeError, "_selftest_poison_pointer needs a Struct");
                    throw PythonErrorSet{};
                }
                std::wstring name;
                if (!wide_from_py(name_object, name)) throw PythonErrorSet{};
                objects::properties::poison_object_pointer(target, name, mode);
                Py_RETURN_NONE;
            });
        }

        // TEST ONLY: runs `function` on `object` with an already built (possibly poisoned) parameter block, through the same
        // guarded path as a normal call.
        PyObject* selftest_call_with_block(PyObject*, PyObject* args)
        {
            return guard_object([&]() -> PyObject* {
                PyObject* object_arg = nullptr;
                PyObject* function_arg = nullptr;
                PyObject* block_arg = nullptr;
                if (!PyArg_ParseTuple(args, "OOO:_selftest_call_with_block", &object_arg, &function_arg, &block_arg)) throw PythonErrorSet{};
                objects::ObjectRef object;
                objects::ObjectRef function;
                objects::properties::Target block;
                if (!object_ref_from_py(object_arg, object) || !object_ref_from_py(function_arg, function) || !struct_target_from_py(block_arg, block))
                {
                    PyErr_SetString(PyExc_TypeError, "_selftest_call_with_block(object, function, block) needs a UObject, a UFunction and a Struct");
                    throw PythonErrorSet{};
                }
                {
                    GilRelease unlocked;
                    objects::properties::call_function(objects::api::call_context(object, function), function, block);
                }
                Py_RETURN_NONE;
            });
        }

        PyObject* selftest_faults_survived(PyObject*, PyObject*)
        {
            return PyLong_FromUnsignedLongLong(objects::properties::faults_survived());
        }

        // Checks the object registry against a real engine object (docs/LIFETIME.md).
        PyObject* selftest_objects(PyObject*, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                const objects::SelfTestResult result = objects::run_self_test();
                PyObject* dict = PyDict_New();
                if (!dict) throw PythonErrorSet{};

                const auto put = [&](const char* key, PyObject* value) {
                    if (!value || PyDict_SetItemString(dict, key, value) != 0)
                    {
                        Py_XDECREF(value);
                        Py_DECREF(dict);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(value);
                };
                put("object_name", py_from_wide(result.object_name));
                put("resolved", PyBool_FromLong(result.resolved));
                put("stale_rejected", PyBool_FromLong(result.stale_rejected));
                put("slot_mismatch_rejected", PyBool_FromLong(result.slot_mismatch_rejected));
                put("out_of_range_rejected", PyBool_FromLong(result.out_of_range_rejected));
                put("null_rejected", PyBool_FromLong(result.null_rejected));
                put("destroyed_message_ok", PyBool_FromLong(result.destroyed_message_ok));
                put("deleted_total", PyLong_FromUnsignedLongLong(result.stats.deleted_total));
                put("deleted_on_game_thread", PyLong_FromUnsignedLongLong(result.stats.deleted_on_game_thread));
                put("deleted_off_game_thread", PyLong_FromUnsignedLongLong(result.stats.deleted_off_game_thread));
                put("deleted_before_game_thread_known", PyLong_FromUnsignedLongLong(result.stats.deleted_before_game_thread_known));
                return dict;
            });
        }

        // Diagnostics for docs/THREADING.md: which OS threads reach which engine entry points.
        PyObject* selftest_thread_probe(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                const int start = PyObject_IsTrue(arg);
                if (start < 0) throw PythonErrorSet{};
                if (start) thread_probe::start();

                PyObject* sites = PyDict_New();
                PyObject* dict = PyDict_New();
                if (!sites || !dict) throw PythonErrorSet{};
                for (const auto& report : thread_probe::snapshot())
                {
                    PyObject* ids = PyList_New(0);
                    for (const auto id : report.thread_ids) PyList_Append(ids, PyLong_FromUnsignedLong(id));
                    PyObject* entry = Py_BuildValue("{s:K,s:N}", "calls", static_cast<unsigned long long>(report.calls), "threads", ids);
                    PyDict_SetItemString(sites, report.name, entry);
                    Py_XDECREF(entry);
                }
                PyDict_SetItemString(dict, "sites", sites);
                Py_DECREF(sites);
                PyObject* current = PyLong_FromUnsignedLong(thread_probe::current_thread_id());
                PyObject* game = PyLong_FromUnsignedLong(thread_probe::game_thread_id());
                PyDict_SetItemString(dict, "calling_thread", current);
                PyDict_SetItemString(dict, "game_thread", game);
                Py_XDECREF(current);
                Py_XDECREF(game);
                return dict;
            });
        }

        PyObject* static_find_object(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring path;
                if (!wide_from_py(arg, path)) throw PythonErrorSet{};
                return make_py_object(objects::api::find_by_path(path));
            });
        }

        PyObject* find_first_of(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring class_name;
                if (!wide_from_py(arg, class_name)) throw PythonErrorSet{};
                return make_py_object(objects::api::find_first_of(class_name));
            });
        }

        PyObject* object_at_address(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                if (!PyLong_Check(arg))
                {
                    PyErr_Format(PyExc_TypeError, "address must be an int, not %.200s", Py_TYPE(arg)->tp_name);
                    throw PythonErrorSet{};
                }
                const unsigned long long address = PyLong_AsUnsignedLongLong(arg);
                if (PyErr_Occurred()) throw PythonErrorSet{};   // negative or too large
                return make_py_object(objects::api::object_at_address(static_cast<std::uintptr_t>(address)));
            });
        }

        // make_struct(type, **fields): a new struct value that owns its memory.
        PyObject* make_struct(PyObject*, PyObject* args, PyObject* kwargs)
        {
            return guard_object([&]() -> PyObject* {
                PyObject* type_argument = nullptr;
                if (!PyArg_ParseTuple(args, "O:make_struct", &type_argument)) throw PythonErrorSet{};

                objects::ObjectRef type_ref;
                if (!object_ref_from_py(type_argument, type_ref))
                {
                    if (!PyUnicode_Check(type_argument))
                    {
                        PyErr_Format(PyExc_TypeError, "make_struct() needs a UScriptStruct or a struct path str, not %.200s", Py_TYPE(type_argument)->tp_name);
                        throw PythonErrorSet{};
                    }
                    std::wstring path;
                    if (!wide_from_py(type_argument, path)) throw PythonErrorSet{};
                    type_ref = objects::api::find_by_path(path);
                    if (type_ref.handle.index < 0) throw UEError("no struct type with that path");
                }

                const objects::properties::Target target = objects::properties::make_struct(type_ref);
                PyObject* result = make_py_struct(target);
                if (!result) throw PythonErrorSet{};
                if (kwargs && PyDict_Size(kwargs) > 0)
                {
                    try
                    {
                        properties_assign_fields(target, kwargs);
                    }
                    catch (...)
                    {
                        Py_DECREF(result); // drops the half-built struct
                        throw;
                    }
                }
                return result;
            });
        }

        // Counts for the owned-struct leak check (created, destroyed, queued, leaked, live).
        PyObject* selftest_struct_stats(PyObject*, PyObject*)
        {
            return guard_object([&]() -> PyObject* {
                const objects::properties::OwnedStructStats stats = objects::properties::owned_struct_stats();
                PyObject* dict = PyDict_New();
                if (!dict) throw PythonErrorSet{};
                const auto put = [&](const char* key, unsigned long long number) {
                    PyObject* value = PyLong_FromUnsignedLongLong(number);
                    if (!value || PyDict_SetItemString(dict, key, value) != 0)
                    {
                        Py_XDECREF(value);
                        Py_DECREF(dict);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(value);
                };
                put("created", stats.created);
                put("destroyed", stats.destroyed);
                put("queued", stats.queued);
                put("leaked_inner", stats.leaked_inner);
                put("leaked_block", stats.leaked_block);
                put("live", stats.created - stats.destroyed);
                return dict;
            });
        }

        // How much the bind_event broadcast callbacks cost (docs: DelegateApi BroadcastProfile).
        auto selftest_event_profile(PyObject* self, PyObject* args) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                int reset = 0;
                if (!PyArg_ParseTuple(args, "|i:_selftest_event_profile", &reset)) throw PythonErrorSet{};
                if (reset) delegates::reset_broadcast_profile();
                const delegates::BroadcastProfile p = delegates::broadcast_profile();
                PyObject* dict = PyDict_New();
                if (!dict) throw PythonErrorSet{};
                const auto put = [&](const char* key, unsigned long long number) {
                    PyObject* value = PyLong_FromUnsignedLongLong(number);
                    if (!value || PyDict_SetItemString(dict, key, value) != 0)
                    {
                        Py_XDECREF(value);
                        Py_DECREF(dict);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(value);
                };
                put("process_events_seen", p.process_events_seen);
                put("script_calls_seen", p.script_calls_seen);
                put("matched", p.matched);
                put("total_ns", p.total_ns);
                put("reset_count", p.reset_count);
                return dict;
            });
        }

        // Manual-detour self test (bxc): does a plain PLH detour on ProcessEvent fire at all in this game?
        auto selftest_manual_pe_detour(PyObject*, PyObject*) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                const bool armed = delegates::install_manual_pe_detour();
                if (armed) Py_RETURN_TRUE;
                Py_RETURN_FALSE;
            });
        }

        auto selftest_manual_pe_detour_calls(PyObject*, PyObject*) -> PyObject*
        {
            return PyLong_FromUnsignedLongLong(delegates::manual_pe_detour_calls());
        }

        // Wildcard diagnostic (bxc): record/return the names of delegate-signature functions dispatched.
        auto selftest_delegate_broadcast_diag(PyObject*, PyObject* args) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                int on = 0;
                if (!PyArg_ParseTuple(args, "i:_selftest_delegate_broadcast_diag", &on)) throw PythonErrorSet{};
                delegates::set_delegate_broadcast_diag(on != 0);
                Py_RETURN_NONE;
            });
        }

        auto selftest_delegate_broadcasts(PyObject*, PyObject*) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                PyObject* list = PyList_New(0);
                if (!list) throw PythonErrorSet{};
                for (const std::wstring& name : delegates::recorded_delegate_broadcasts())
                {
                    PyObject* item = PyUnicode_FromWideChar(name.c_str(), static_cast<Py_ssize_t>(name.size()));
                    if (!item || PyList_Append(list, item) != 0)
                    {
                        Py_XDECREF(item);
                        Py_DECREF(list);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(item);
                }
                return list;
            });
        }

        auto selftest_process_event_dump(PyObject*, PyObject* args) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                int on = 0;
                if (!PyArg_ParseTuple(args, "i:_selftest_process_event_dump", &on)) throw PythonErrorSet{};
                delegates::set_process_event_dump(on != 0);
                Py_RETURN_NONE;
            });
        }

        auto selftest_process_event_dump_read(PyObject*, PyObject*) -> PyObject*
        {
            return guard_object([&]() -> PyObject* {
                PyObject* list = PyList_New(0);
                if (!list) throw PythonErrorSet{};
                for (const auto& entry : delegates::recorded_process_event_dump())
                {
                    PyObject* name = PyUnicode_FromWideChar(entry.name.c_str(), static_cast<Py_ssize_t>(entry.name.size()));
                    PyObject* item = name ? Py_BuildValue("OKK", name, entry.process_events, entry.script_calls) : nullptr;
                    Py_XDECREF(name);
                    if (!item || PyList_Append(list, item) != 0)
                    {
                        Py_XDECREF(item);
                        Py_DECREF(list);
                        throw PythonErrorSet{};
                    }
                    Py_DECREF(item);
                }
                return list;
            });
        }

        auto objects_to_list(const std::vector<objects::ObjectRef>& found) -> PyObject*
        {
            PyObject* list = PyList_New(0);
            if (!list) throw PythonErrorSet{};
            for (const objects::ObjectRef& ref : found)
            {
                PyObject* item = make_py_object(ref);
                if (!item || PyList_Append(list, item) != 0)
                {
                    Py_XDECREF(item);
                    Py_DECREF(list);
                    throw PythonErrorSet{};
                }
                Py_DECREF(item);
            }
            return list;
        }

        PyObject* find_all_of(PyObject*, PyObject* arg)
        {
            return guard_object([&]() -> PyObject* {
                std::wstring class_name;
                if (!wide_from_py(arg, class_name)) throw PythonErrorSet{};
                return objects_to_list(objects::api::find_all_of(class_name));
            });
        }

        PyObject* find_objects(PyObject*, PyObject* args, PyObject* kwargs)
        {
            return guard_object([&]() -> PyObject* {
                static const char* keywords[] = {"class_name", "name", "required_flags", "banned_flags", "exact_class", "limit", nullptr};
                PyObject* class_object = Py_None;
                PyObject* name_object = Py_None;
                long long required_flags = 0;
                long long banned_flags = 0;
                int exact_class = 0;
                Py_ssize_t limit = 0;
                if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOLLpn:find_objects", const_cast<char**>(keywords), &class_object, &name_object, &required_flags, &banned_flags, &exact_class, &limit))
                {
                    throw PythonErrorSet{};
                }
                if (required_flags < 0 || required_flags > INT32_MAX || banned_flags < 0 || banned_flags > INT32_MAX)
                {
                    PyErr_SetString(PyExc_ValueError, "object flags must be between 0 and 2**31 - 1");
                    throw PythonErrorSet{};
                }
                if (limit < 0)
                {
                    PyErr_SetString(PyExc_ValueError, "limit must not be negative (0 means no limit)");
                    throw PythonErrorSet{};
                }

                std::optional<std::wstring> class_name;
                std::optional<std::wstring> object_name;
                if (class_object != Py_None)
                {
                    std::wstring text;
                    if (!wide_from_py(class_object, text)) throw PythonErrorSet{};
                    class_name = std::move(text);
                }
                if (name_object != Py_None)
                {
                    std::wstring text;
                    if (!wide_from_py(name_object, text)) throw PythonErrorSet{};
                    object_name = std::move(text);
                }
                if (!class_name && !object_name)
                {
                    PyErr_SetString(PyExc_ValueError, "find_objects() needs class_name, name, or both");
                    throw PythonErrorSet{};
                }

                return objects_to_list(objects::api::find_objects(class_name ? std::optional<std::wstring_view>(*class_name) : std::nullopt,
                                                                  object_name ? std::optional<std::wstring_view>(*object_name) : std::nullopt,
                                                                  static_cast<std::int32_t>(required_flags),
                                                                  static_cast<std::int32_t>(banned_flags),
                                                                  exact_class != 0,
                                                                  static_cast<std::size_t>(limit)));
            });
        }

        PyObject* is_game_thread(PyObject*, PyObject*)
        {
            if (game_thread_executor::on_game_thread()) Py_RETURN_TRUE;
            Py_RETURN_FALSE;
        }

        // Python-level helpers that sit on top of the C functions above.
        constexpr const char* s_bootstrap_source = R"PY(
import concurrent.futures as _futures  # importing here makes the initialising thread threading's main thread
import enum as _enum


def _load_blueprint_class(path):
    """Loads the class of a Blueprint function path (/Game/Folder/BP_X.BP_X_C:Function) when the game has not loaded it. True if the class exists afterwards."""
    class_path = path.partition(":")[0]
    if not (class_path.startswith("/Game/") and class_path.endswith("_C")):
        return False
    if static_find_object(class_path) is not None:
        return True
    library = static_find_object("/Script/Engine.KismetSystemLibrary")
    try:
        return library.LoadClassAsset_Blocking(class_path) is not None
    except UEError:
        return False


def register_hook(path, pre=None, post=None, instance=None):
    """register_hook(path, pre=None, post=None, instance=None) -> (pre_id, post_id): call pre(context, params) before and post(context, params, result) after every call of the function at path, for example /Script/Engine.Actor:ReceiveBeginPlay. With instance=obj only calls on that object reach the callbacks (other objects cost nothing). A Blueprint function path (/Game/Folder/BP_X.BP_X_C:Function) loads the class first when the game has not loaded it. result is the function's return value (None if it returns nothing); do not read params.ReturnValue in a post-hook, it is stale. A post-hook that returns a value replaces the result (return ue4ss.NULL to set a null object); returning None keeps it. params is only valid inside the callback; params.copy() keeps a value."""
    if isinstance(path, str) and path.startswith("/Game/") and static_find_object(path) is None:
        _load_blueprint_class(path)
    return _register_hook(path, pre, post, instance)


def _enable_submodules():
    """Let `import ue4ss.engine` find ue4ss/engine.py next to the interpreter's libraries (the mod's dlls folder): ue4ss is built in, so it
    becomes a package by getting a __path__."""
    import os as _os
    import sys as _sys
    folders = [_os.path.join(entry, "ue4ss") for entry in _sys.path if entry and _os.path.isdir(_os.path.join(entry, "ue4ss"))]
    globals()["__path__"] = folders


_enable_submodules()


def _reload_cleanup(scripts_dir):
    """Forget what the previous run of main.py left in Python: its modules and the names in __main__. Returns the removed module names."""
    import gc as _gc
    import importlib as _importlib
    import os as _os
    import sys as _sys
    prefix = _os.path.normcase(_os.path.abspath(scripts_dir)) + _os.sep
    removed = []
    for name, module in list(_sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name in ("__main__", "ue4ss") or not isinstance(path, str):
            continue
        if _os.path.normcase(_os.path.abspath(path)).startswith(prefix):
            del _sys.modules[name]
            removed.append(name)
    namespace = _sys.modules["__main__"].__dict__
    keep = {"__name__", "__doc__", "__package__", "__loader__", "__spec__", "__builtins__", "__file__", "__annotations__"}
    for key in list(namespace):
        if key not in keep:
            del namespace[key]
    _importlib.invalidate_caches()
    _gc.collect()
    return removed


class Key(_enum.IntEnum):
    """Keys for register_key_down (Windows virtual-key codes, names as in UE4SS). Aliases share a number."""

    LEFT_MOUSE_BUTTON = 0x1
    RIGHT_MOUSE_BUTTON = 0x2
    CANCEL = 0x3
    MIDDLE_MOUSE_BUTTON = 0x4
    XBUTTON_ONE = 0x5
    XBUTTON_TWO = 0x6
    BACKSPACE = 0x8
    TAB = 0x9
    CLEAR = 0x0C
    RETURN = 0x0D
    PAUSE = 0x13
    CAPS_LOCK = 0x14
    IME_KANA = 0x15
    IME_HANGUEL = 0x15
    IME_HANGUL = 0x15
    IME_ON = 0x16
    IME_JUNJA = 0x17
    IME_FINAL = 0x18
    IME_HANJA = 0x19
    IME_KANJI = 0x19
    IME_OFF = 0x1A
    ESCAPE = 0x1B
    IME_CONVERT = 0x1C
    IME_NONCONVERT = 0x1D
    IME_ACCEPT = 0x1E
    IME_MODECHANGE = 0x1F
    SPACE = 0x20
    PAGE_UP = 0x21
    PAGE_DOWN = 0x22
    END = 0x23
    HOME = 0x24
    LEFT_ARROW = 0x25
    UP_ARROW = 0x26
    RIGHT_ARROW = 0x27
    DOWN_ARROW = 0x28
    SELECT = 0x29
    PRINT = 0x2A
    EXECUTE = 0x2B
    PRINT_SCREEN = 0x2C
    INS = 0x2D
    DEL = 0x2E
    HELP = 0x2F
    ZERO = 0x30
    ONE = 0x31
    TWO = 0x32
    THREE = 0x33
    FOUR = 0x34
    FIVE = 0x35
    SIX = 0x36
    SEVEN = 0x37
    EIGHT = 0x38
    NINE = 0x39
    A = 0x41
    B = 0x42
    C = 0x43
    D = 0x44
    E = 0x45
    F = 0x46
    G = 0x47
    H = 0x48
    I = 0x49
    J = 0x4A
    K = 0x4B
    L = 0x4C
    M = 0x4D
    N = 0x4E
    O = 0x4F
    P = 0x50
    Q = 0x51
    R = 0x52
    S = 0x53
    T = 0x54
    U = 0x55
    V = 0x56
    W = 0x57
    X = 0x58
    Y = 0x59
    Z = 0x5A
    LEFT_WIN = 0x5B
    RIGHT_WIN = 0x5C
    APPS = 0x5D
    SLEEP = 0x5F
    NUM_ZERO = 0x60
    NUM_ONE = 0x61
    NUM_TWO = 0x62
    NUM_THREE = 0x63
    NUM_FOUR = 0x64
    NUM_FIVE = 0x65
    NUM_SIX = 0x66
    NUM_SEVEN = 0x67
    NUM_EIGHT = 0x68
    NUM_NINE = 0x69
    MULTIPLY = 0x6A
    ADD = 0x6B
    SEPARATOR = 0x6C
    SUBTRACT = 0x6D
    DECIMAL = 0x6E
    DIVIDE = 0x6F
    F1 = 0x70
    F2 = 0x71
    F3 = 0x72
    F4 = 0x73
    F5 = 0x74
    F6 = 0x75
    F7 = 0x76
    F8 = 0x77
    F9 = 0x78
    F10 = 0x79
    F11 = 0x7A
    F12 = 0x7B
    F13 = 0x7C
    F14 = 0x7D
    F15 = 0x7E
    F16 = 0x7F
    F17 = 0x80
    F18 = 0x81
    F19 = 0x82
    F20 = 0x83
    F21 = 0x84
    F22 = 0x85
    F23 = 0x86
    F24 = 0x87
    NUM_LOCK = 0x90
    SCROLL_LOCK = 0x91
    BROWSER_BACK = 0xA6
    BROWSER_FORWARD = 0xA7
    BROWSER_REFRESH = 0xA8
    BROWSER_STOP = 0xA9
    BROWSER_SEARCH = 0xAA
    BROWSER_FAVORITES = 0xAB
    BROWSER_HOME = 0xAC
    VOLUME_MUTE = 0xAD
    VOLUME_DOWN = 0xAE
    VOLUME_UP = 0xAF
    MEDIA_NEXT_TRACK = 0xB0
    MEDIA_PREV_TRACK = 0xB1
    MEDIA_STOP = 0xB2
    MEDIA_PLAY_PAUSE = 0xB3
    LAUNCH_MAIL = 0xB4
    LAUNCH_MEDIA_SELECT = 0xB5
    LAUNCH_APP1 = 0xB6
    LAUNCH_APP2 = 0xB7
    OEM_ONE = 0xBA
    OEM_PLUS = 0xBB
    OEM_COMMA = 0xBC
    OEM_MINUS = 0xBD
    OEM_PERIOD = 0xBE
    OEM_TWO = 0xBF
    OEM_THREE = 0xC0
    OEM_FOUR = 0xDB
    OEM_FIVE = 0xDC
    OEM_SIX = 0xDD
    OEM_SEVEN = 0xDE
    OEM_EIGHT = 0xDF
    OEM_102 = 0xE2
    IME_PROCESS = 0xE5
    PACKET = 0xE7
    ATTN = 0xF6
    CRSEL = 0xF7
    EXSEL = 0xF8
    EREOF = 0xF9
    PLAY = 0xFA
    ZOOM = 0xFB
    NONAME = 0xFC
    PA1 = 0xFD


class ModifierKey(_enum.IntEnum):
    """Modifier keys for register_key_down(modifiers=...)."""

    SHIFT = 0x10
    CONTROL = 0x11
    ALT = 0x12


class ObjectFlags(_enum.IntFlag):
    """Engine object flags (EObjectFlags) for find_objects(required_flags=..., banned_flags=...)."""

    RF_Public = 0x00000001
    RF_Standalone = 0x00000002
    RF_Transactional = 0x00000008
    RF_ClassDefaultObject = 0x00000010
    RF_ArchetypeObject = 0x00000020
    RF_Transient = 0x00000040
    RF_MarkAsRootSet = 0x00000080
    RF_NeedLoad = 0x00000400
    RF_BeginDestroyed = 0x00008000
    RF_FinishDestroyed = 0x00010000
    RF_DefaultSubObject = 0x00040000
    RF_WasLoaded = 0x00080000


def call_in_game_thread(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on the game thread; returns a concurrent.futures.Future for the result."""
    if not callable(fn):
        raise TypeError("fn must be callable")
    future = _futures.Future()

    def run():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as e:
            future.set_exception(e)

    execute_in_game_thread(run)
    return future


def make_transform(location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), order="unreal", degrees=True):
    """make_transform(location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), order="unreal", degrees=True) -> Struct: an owned FTransform. location and scale are (x, y, z) or {"X", "Y", "Z"}; rotation is Euler angles (pitch, yaw, roll for the default order, see euler_to_quat) or an X/Y/Z/W quaternion dict."""

    def vector(value):
        if isinstance(value, dict):
            return value
        x, y, z = value
        return {"X": x, "Y": y, "Z": z}

    quaternion = rotation if isinstance(rotation, dict) else euler_to_quat(*rotation, order=order, degrees=degrees)
    return make_struct("/Script/CoreUObject.Transform", Translation=vector(location), Rotation=quaternion, Scale3D=vector(scale))
)PY";

        PyMethodDef s_methods[] = {
                {"execute_in_game_thread", execute_in_game_thread, METH_O, "execute_in_game_thread(fn): run fn() on the game thread at a later tick. Any thread."},
                {"execute_with_delay", execute_with_delay, METH_VARARGS, "execute_with_delay(milliseconds, fn): run fn() on the game thread after at least that delay (accurate to one frame)."},
                {"make_struct", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(make_struct)), METH_VARARGS | METH_KEYWORDS, "make_struct(type, **fields) -> Struct: a new struct value that owns its memory; type is a UScriptStruct or a path such as /Script/CoreUObject.Vector."},
                {"is_game_thread", is_game_thread, METH_NOARGS, "True when called on the game thread."},
                {"_register_hook", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(py_register_hook)), METH_VARARGS | METH_KEYWORDS,
                 "register_hook(path, pre=None, post=None, instance=None) -> (pre_id, post_id): call pre(context, params) before and post(context, params, result) after every call of the function at path, for example /Script/Engine.Actor:ReceiveBeginPlay. With instance=obj only calls on that object reach the callbacks (other objects cost nothing). A Blueprint function path (/Game/Folder/BP_X.BP_X_C:Function) loads the class first when the game has not loaded it. result is the function's return value (None if it returns nothing); do not read params.ReturnValue in a post-hook, it is stale. A post-hook that returns a value replaces the result (return ue4ss.NULL to set a null object); returning None keeps it. params is only valid inside the callback; params.copy() keeps a value."},
                {"register_replacement", py_register_replacement, METH_VARARGS,
                 "register_replacement(path, callback) -> id: callback(context, params) runs INSTEAD of the function at path and returns its result (None for no result, ue4ss.NULL for a null object). Return ue4ss.CALL_ORIGINAL to let the function run normally; an exception in the callback also lets it run. params holds the call's parameters, so outputs can be set on it. Calling the replaced function from inside the callback runs the original. Only calls that go through ProcessEvent are replaced (C++ callers, delegates, events, timers, Python calls); a Blueprint script calling a native function is not. One replacement per function."},
                {"unregister_replacement", py_unregister_replacement, METH_VARARGS, "unregister_replacement(id): remove a replacement; id is what register_replacement returned."},
                {"euler_to_quat", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(py_euler_to_quat)), METH_VARARGS | METH_KEYWORDS,
                 "euler_to_quat(a, b, c, order=\"unreal\", degrees=True) -> {\"X\", \"Y\", \"Z\", \"W\"}: the quaternion for Euler angles. The default order \"unreal\" takes (pitch, yaw, roll) like FRotator (pitch about Y, yaw about Z, roll about X). An axis order such as \"xyz\" or \"zyx\" means three intrinsic rotations in that order, the angles given in the same order. degrees=False takes radians."},
                {"quat_to_euler", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(py_quat_to_euler)), METH_VARARGS | METH_KEYWORDS,
                 "quat_to_euler(quat, order=\"unreal\", degrees=True) -> (a, b, c): the Euler angles of a quaternion (a Quat struct, an X/Y/Z/W dict or a sequence of four numbers); the inverse of euler_to_quat."},
                {"set_slow_callback_warning", slow::py_set_slow_callback_warning, METH_VARARGS,
                 "set_slow_callback_warning(milliseconds): warn (in the log, at most 3 times per callback) when a Python callback on the game thread takes at least this long: game-thread tasks, hooks, replacements and new-object callbacks. Default 50; 0 switches the warnings off."},
                {"unregister_hook", py_unregister_hook, METH_VARARGS, "unregister_hook(ids) or unregister_hook(pre_id, post_id): remove a hook; ids is what register_hook returned."},
                {"bind_event", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(py_bind_event)), METH_VARARGS | METH_KEYWORDS, "bind_event(object, name, callback, async_=False) -> id: call callback(context, params) on the game thread each time the multicast delegate property `name` broadcasts. With async_=True, snapshot params on the game thread and run the callback on a worker; only the owned snapshot is safe to read there and Unreal object access raises UEError. params.copy() keeps a blocking-callback value. First supported for multicast properties only; single-cast and sparse delegates are not bindable yet."},
                {"unbind_event", py_unbind_event, METH_VARARGS, "unbind_event(id): stop delivering broadcasts to a bind_event binding; id is what bind_event returned."},
                {"notify_on_new_object", py_notify_on_new_object, METH_VARARGS, "notify_on_new_object(class_path, callback) -> id: call callback(obj) on the game thread for every new instance of the class (or a subclass; default objects too), for example /Script/Engine.Actor. The call comes shortly after creation, not during it; an object destroyed before then is not delivered."},
                {"reload", reload::py_reload, METH_NOARGS, "reload(): at the next game tick, unregister every hook, replacement, key binding and new-object notification, drop queued and delayed tasks and the modules imported from python_scripts, clear the names main.py defined and run main.py again. Python threads you started keep running (check script_generation() in them). Several calls before the tick make one reload."},
                {"script_generation", reload::py_script_generation, METH_NOARGS, "script_generation() -> int: 1 while the first run of main.py is current, plus one for every reload."},
                {"register_key_down", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(py_register_key_down)), METH_VARARGS | METH_KEYWORDS, "register_key_down(key, callback, modifiers=()) -> id: call callback() on the game thread each time the key (a ue4ss.Key) goes down while the game window has the focus. modifiers is an iterable of ue4ss.ModifierKey that must be held, exactly those. Several callbacks may share a key."},
                {"unregister_key_down", py_unregister_key_down, METH_VARARGS, "unregister_key_down(id): stop a register_key_down binding."},
                {"_selftest_fire_key", py_selftest_fire_key, METH_VARARGS, "Internal: act as if the key (with exactly these modifiers) went down; returns how many bindings will run."},
                {"unregister_notify_on_new_object", py_unregister_notify_on_new_object, METH_VARARGS, "unregister_notify_on_new_object(id): stop a notify_on_new_object subscription."},
                {"static_find_object", static_find_object, METH_O, "static_find_object(path) -> UObject | None: the object with that full path, for example /Script/Engine.Actor."},
                {"find_all_of", find_all_of, METH_O, "find_all_of(class_name) -> list[UObject]: every live instance of the class or a subclass; default objects and archetypes are not instances and are left out (same as find_objects(class_name, banned_flags=RF_ClassDefaultObject | RF_ArchetypeObject))."},
                {"find_objects", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(find_objects)), METH_VARARGS | METH_KEYWORDS,
                 "find_objects(class_name=None, name=None, required_flags=0, banned_flags=0, exact_class=False, limit=0) -> list[UObject]: objects matching the class (and subclasses unless exact_class), the object name, or both. Includes default objects unless banned_flags has ObjectFlags.RF_ClassDefaultObject. limit 0 means no limit."},
                {"object_at_address", object_at_address, METH_O, "object_at_address(address) -> UObject | None: the live object at this address (for example a pointer read from Blueprint bytecode), or None if no object lives there. Never faults, whatever the number."},
                {"find_first_of", find_first_of, METH_O, "find_first_of(class_name) -> UObject | None: first instance of the class or a subclass (not the default object)."},
                {"_selftest_struct_stats", selftest_struct_stats, METH_NOARGS, "Internal: counts of owned structs created, destroyed, queued for destruction and leaked."},
                {"_selftest_poison_pointer", selftest_poison_pointer, METH_VARARGS, "Internal: overwrite an object pointer field of a Struct with a bad value (mode 0 freed heap, 1 unmapped, 2 inside a live object, 3 misaligned, 4 small integer); reading it must give None."},
                {"_selftest_call_with_block", selftest_call_with_block, METH_VARARGS, "Internal: call function on object with a prebuilt parameter block (used to prove that a fault inside the game becomes a UEError)."},
                {"_selftest_faults_survived", selftest_faults_survived, METH_NOARGS, "Internal: how many hardware faults inside engine calls were turned into UEError."},
                {"_selftest_event_profile", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(selftest_event_profile)), METH_VARARGS, "Internal: what the bind_event broadcast callbacks cost (ProcessEvent and script calls seen, how many matched a bound signature, cumulative time); pass 1 to reset the counters."},
                {"_selftest_manual_pe_detour", selftest_manual_pe_detour, METH_NOARGS, "Internal: install a manual PLH detour on UObject::ProcessEvent with a plain counter callback (no UEPseudo dispatch). Returns True if armed."},
                {"_selftest_manual_pe_detour_calls", selftest_manual_pe_detour_calls, METH_NOARGS, "Internal: how many times the manual ProcessEvent detour fired (0 if not armed)."},
                {"_selftest_delegate_broadcast_diag", selftest_delegate_broadcast_diag, METH_VARARGS, "Internal: pass 1 to record the full names of every __DelegateSignature function dispatched through the global channels (wildcard capture); pass 0 to stop and clear."},
                {"_selftest_delegate_broadcasts", selftest_delegate_broadcasts, METH_NOARGS, "Internal: the recorded __DelegateSignature function names (bounded list), when the wildcard capture is on."},
                {"_selftest_process_event_dump", selftest_process_event_dump, METH_VARARGS, "Internal: pass 1 to record unique ProcessEvent/local-script function names with counts; pass 0 to stop recording."},
                {"_selftest_process_event_dump_read", selftest_process_event_dump_read, METH_NOARGS, "Internal: return the ProcessEvent dump as (full_name, process_event_count, local_script_count) tuples."},
                {"_selftest_objects", selftest_objects, METH_NOARGS, "Internal: check the object registry against a real engine object."},
                {"_selftest_thread_probe", selftest_thread_probe, METH_O, "Internal: report which OS threads reach which engine entry points (True starts the probe)."},
                {"_selftest_fstring", selftest_fstring, METH_O, "Internal: round-trip a str through an Unreal FString."},
                {"_selftest_raise", selftest_raise, METH_O, "Internal: raise a C++ exception of the given kind through the error guard."},
                {nullptr, nullptr, 0, nullptr},
        };

        PyModuleDef s_module_def = {
                PyModuleDef_HEAD_INIT,
                "ue4ss",
                "Python bindings for UE4SS.",
                -1,
                s_methods,
        };
    }

    auto set_python_error(const ErrorInfo& info) -> void
    {
        PyObject* ue_error = s_ue_error ? s_ue_error : PyExc_RuntimeError;
        PyObject* invalid_object_error = s_invalid_object_error ? s_invalid_object_error : PyExc_RuntimeError;

        switch (info.kind)
        {
        case ErrorKind::PythonErrorSet:
            if (!PyErr_Occurred()) PyErr_SetString(PyExc_SystemError, "C++ signalled a Python error but none is set");
            return;
        case ErrorKind::InvalidObject:
            PyErr_SetString(invalid_object_error, info.message.c_str());
            return;
        case ErrorKind::PropertyNotFound:
            PyErr_SetString(PyExc_AttributeError, info.message.c_str());
            return;
        case ErrorKind::UE:
            PyErr_SetString(ue_error, info.message.c_str());
            return;
        case ErrorKind::OutOfMemory:
            PyErr_NoMemory();
            return;
        case ErrorKind::Internal:
            PyErr_Format(ue_error, "internal error: %s", info.message.c_str());
            return;
        case ErrorKind::Unknown:
            PyErr_SetString(ue_error, "internal error: unknown C++ exception");
            return;
        }
    }

    auto init_ue4ss_module() -> PyObject*
    {
        PyObject* module = PyModule_Create(&s_module_def);
        if (!module) return nullptr;

        s_ue_error = PyErr_NewExceptionWithDoc("ue4ss.UEError", "Base class for errors raised by the UE4SS bindings.", PyExc_Exception, nullptr);
        if (!s_ue_error) goto fail;
        s_invalid_object_error = PyErr_NewExceptionWithDoc("ue4ss.InvalidObjectError",
                                                           "The Unreal object was destroyed, garbage collected or its slot was reused.",
                                                           s_ue_error,
                                                           nullptr);
        if (!s_invalid_object_error) goto fail;

        {
            const std::string version = std::to_string(version_major) + "." + std::to_string(version_minor);
            if (PyModule_AddStringConstant(module, "__version__", version.c_str()) < 0) goto fail;
        }
        if (PyModule_AddObjectRef(module, "UEError", s_ue_error) < 0) goto fail;
        if (PyModule_AddObjectRef(module, "InvalidObjectError", s_invalid_object_error) < 0) goto fail;

        if (!init_fname_type(module)) goto fail;
        if (!init_uobject_type(module)) goto fail;
        if (!init_struct_type(module)) goto fail;
        if (!init_array_type(module)) goto fail;
        if (!init_fixed_array_type(module)) goto fail;
        if (!init_map_set_types(module)) goto fail;
        if (!init_call_types(module)) goto fail;
        if (!init_hook_types(module)) goto fail;

        {
            PyObject* dict = PyModule_GetDict(module); // borrowed
            PyObject* result = PyRun_String(s_bootstrap_source, Py_file_input, dict, dict);
            if (!result) goto fail;
            Py_DECREF(result);
        }
        return module;

    fail:
        Py_DECREF(module);
        return nullptr;
    }
}
