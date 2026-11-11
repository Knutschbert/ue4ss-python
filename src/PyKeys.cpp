#include "PyKeys.hpp"

#include <array>
#include <map>
#include <memory>
#include <mutex>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>

#include <DynamicOutput/DynamicOutput.hpp>
#include <Input/KeyDef.hpp>
#include <UE4SSProgram.hpp>

#include <pyue/errors.hpp>
#include <pyue/key_bindings.hpp>

#include "GameThreadExecutor.hpp"
#include "Gil.hpp"
#include "SlowCallback.hpp"

using namespace RC;

namespace pyue
{
    namespace
    {
        struct Record
        {
            PyObjectRef callback{};
        };

        KeyBindings s_bindings;
        std::mutex s_mutex;
        std::map<int, std::shared_ptr<Record>> s_records;

        // Game thread: run every callback that is bound to this key and these modifiers right now.
        auto dispatch(std::uint8_t key, std::uint8_t modifiers) -> void
        {
            if (!python_alive()) return;
            for (const int id : s_bindings.active(key, modifiers))
            {
                std::shared_ptr<Record> record;
                {
                    std::lock_guard lock{s_mutex};
                    const auto found = s_records.find(id);
                    if (found == s_records.end()) continue; // unregistered by an earlier callback of this same key press
                    record = found->second;
                }
                GilGuard gil;
                slow::Scope timing("key callback");
                PyObject* result = PyObject_CallNoArgs(record->callback.get());
                if (result)
                {
                    Py_DECREF(result);
                    continue;
                }
                if (PyErr_ExceptionMatches(PyExc_SystemExit))
                {
                    PyErr_Clear();
                    PySys_WriteStderr("sys.exit() inside a key callback is ignored\n");
                }
                else
                {
                    PyErr_Print();
                }
            }
        }

        // UE4SS's input thread (not the game thread) calls this: hand over to the game thread.
        auto on_native_key(std::uint8_t key, std::uint8_t modifiers) -> void
        {
            game_thread_executor::post([key, modifiers] { dispatch(key, modifiers); });
        }

        auto register_native(std::uint8_t key, std::uint8_t modifiers, const std::array<int, 3>& modifier_keys, int modifier_count) -> void
        {
            // Consume any press made BEFORE this registration. UE4SS's Win32 input source polls every
            // subscribed key with GetAsyncKeyState and treats any non-zero result as "the key went down",
            // and GetAsyncKeyState is also non-zero for a press made since the last read even if the key is
            // up now - so without this, the first poll after register_key_down could fire for an old press.
            // One read clears the stale bit before we install the handler.
            GetAsyncKeyState(key);
            auto handler = [key, modifiers] { on_native_key(key, modifiers); };
            auto& program = UE4SSProgram::get_program();
            if (modifier_count == 0)
            {
                program.register_keydown_event(static_cast<Input::Key>(key), handler);
                return;
            }
            Input::Handler::ModifierKeyArray array{};
            for (int i = 0; i < modifier_count; ++i) array[static_cast<std::size_t>(i)] = static_cast<Input::ModifierKey>(modifier_keys[static_cast<std::size_t>(i)]);
            program.register_keydown_event(static_cast<Input::Key>(key), array, handler);
        }

        // The key number and the modifier set from the Python arguments; raises ValueError / TypeError.
        struct Parsed
        {
            std::uint8_t key{0};
            std::uint8_t mask{0};
            std::array<int, 3> modifier_keys{};
            int modifier_count{0};
        };

        auto parse_key(PyObject* key_object, PyObject* modifiers_object) -> Parsed
        {
            Parsed parsed;
            const long key = PyLong_AsLong(key_object);
            if (key == -1 && PyErr_Occurred()) throw PythonErrorSet{};
            if (key < 1 || key > 0xFE || KeyBindings::modifier_bit(static_cast<int>(key)) != 0)
            {
                PyErr_SetString(PyExc_ValueError, "key must be a ue4ss.Key (or its number); Shift, Control and Alt are modifiers: pass them in modifiers=");
                throw PythonErrorSet{};
            }
            parsed.key = static_cast<std::uint8_t>(key);
            if (modifiers_object && modifiers_object != Py_None)
            {
                PyObject* iterator = PyObject_GetIter(modifiers_object);
                if (!iterator) throw PythonErrorSet{};
                while (PyObject* item = PyIter_Next(iterator))
                {
                    const long value = PyLong_AsLong(item);
                    Py_DECREF(item);
                    if (value == -1 && PyErr_Occurred())
                    {
                        Py_DECREF(iterator);
                        throw PythonErrorSet{};
                    }
                    const std::uint8_t bit = KeyBindings::modifier_bit(static_cast<int>(value));
                    if (bit == 0)
                    {
                        Py_DECREF(iterator);
                        PyErr_SetString(PyExc_ValueError, "modifiers must be ue4ss.ModifierKey values (SHIFT, CONTROL, ALT)");
                        throw PythonErrorSet{};
                    }
                    if (!(parsed.mask & bit))
                    {
                        parsed.mask |= bit;
                        parsed.modifier_keys[static_cast<std::size_t>(parsed.modifier_count++)] = static_cast<int>(value);
                    }
                }
                Py_DECREF(iterator);
                if (PyErr_Occurred()) throw PythonErrorSet{};
            }
            return parsed;
        }
    }

    PyObject* py_register_key_down(PyObject*, PyObject* args, PyObject* kwargs)
    {
        return guard_object([&]() -> PyObject* {
            PyObject* key_object = nullptr;
            PyObject* callback = nullptr;
            PyObject* modifiers = nullptr;
            static const char* keywords[] = {"key", "callback", "modifiers", nullptr};
            if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|O:register_key_down", const_cast<char**>(keywords), &key_object, &callback, &modifiers)) throw PythonErrorSet{};
            if (!PyCallable_Check(callback))
            {
                PyErr_Format(PyExc_TypeError, "callback must be callable, not %.200s", Py_TYPE(callback)->tp_name);
                throw PythonErrorSet{};
            }
            const Parsed parsed = parse_key(key_object, modifiers);

            auto record = std::make_shared<Record>();
            record->callback = PyObjectRef::borrow(callback);
            const auto added = s_bindings.add(parsed.key, parsed.mask);
            {
                std::lock_guard lock{s_mutex};
                s_records[added.id] = record;
            }
            if (added.needs_native)
            {
                try
                {
                    register_native(parsed.key, parsed.mask, parsed.modifier_keys, parsed.modifier_count);
                }
                catch (...)
                {
                    s_bindings.remove(added.id);
                    std::lock_guard lock{s_mutex};
                    s_records.erase(added.id);
                    throw;
                }
            }
            return PyLong_FromLong(added.id);
        });
    }

    PyObject* py_unregister_key_down(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            int id = 0;
            if (!PyArg_ParseTuple(args, "i:unregister_key_down", &id)) throw PythonErrorSet{};
            if (!s_bindings.remove(id))
            {
                PyErr_SetString(PyExc_ValueError, "no key binding with that id (already unregistered?)");
                throw PythonErrorSet{};
            }
            std::lock_guard lock{s_mutex};
            s_records.erase(id);
            Py_RETURN_NONE;
        });
    }

    PyObject* py_selftest_fire_key(PyObject*, PyObject* args)
    {
        return guard_object([&]() -> PyObject* {
            PyObject* key_object = nullptr;
            PyObject* modifiers = nullptr;
            if (!PyArg_ParseTuple(args, "O|O:_selftest_fire_key", &key_object, &modifiers)) throw PythonErrorSet{};
            const Parsed parsed = parse_key(key_object, modifiers);
            const auto matching = s_bindings.active(parsed.key, parsed.mask).size();
            on_native_key(parsed.key, parsed.mask);   // the same path a real key press takes
            return PyLong_FromSize_t(matching);
        });
    }

    auto unregister_all_key_bindings() -> void
    {
        s_bindings.clear();
        std::lock_guard lock{s_mutex};
        s_records.clear();
    }
}
