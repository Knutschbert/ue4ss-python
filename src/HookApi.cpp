#include "HookApi.hpp"

#include <atomic>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <shared_mutex>
#include <unordered_map>

#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/Hooks/Hooks.hpp>
#include <Unreal/UFunction.hpp>
#include <Unreal/UFunctionStructs.hpp>
#include <Unreal/UObject.hpp>
#include <Unreal/UObjectGlobals.hpp>

#include <pyue/errors.hpp>

#include "GameThreadExecutor.hpp"

using namespace RC;

namespace pyue::hooks
{
    namespace
    {
        // The engine calls this wherever the hooked function runs. Nothing may escape it.
        // Offset of the return parameter inside the function's parameter block, or -1 if it returns nothing.
        auto return_offset_of(Unreal::UFunction* function) -> std::ptrdiff_t
        {
            for (Unreal::FProperty* property : Unreal::TFieldRange<Unreal::FProperty>(function, Unreal::EFieldIterationFlags::None))
            {
                if (property->HasAnyPropertyFlags(Unreal::CPF_ReturnParm)) return property->GetOffset_Internal();
            }
            return -1;
        }

        // A function without parameters and locals is run with a null frame (Locals); there is nothing to read, so hand out an empty block.
        alignas(16) std::uint8_t s_empty_block[64]{};

        auto wrap(const objects::ObjectRef& function, std::ptrdiff_t return_offset, bool no_parameters, HookCallback callback) -> Unreal::UnrealScriptFunctionCallable
        {
            return [function, return_offset, no_parameters, callback = std::move(callback)](Unreal::UnrealScriptFunctionCallableContext& context, void*) {
                if (!callback) return;
                try
                {
                    void* locals = context.TheStack.Locals();
                    if (!locals)
                    {
                        if (!no_parameters) return;
                        locals = s_empty_block;
                    }

                    HookCall call;
                    call.context = objects::acquire(context.Context);
                    objects::properties::ScopedParams params(function, locals);
                    // Out parameters: the callee writes them into the caller's memory through the frame's OutParms list, not into
                    // Locals, so point the view there (reads then see the real output and writes reach the caller).
                    for (Unreal::FOutParmRec* record = context.TheStack.OutParms(); record; record = record->NextOutParm)
                    {
                        params.add_out_parameter(record->Property, record->PropAddr);
                    }
                    call.params = params.target();

                    // RESULT_DECL is where the engine puts the return value. Treat it as the return parameter of a block
                    // whose base is RESULT_DECL minus that parameter's offset, so the ordinary property access reads it.
                    std::optional<objects::properties::ScopedParams> result;
                    if (context.RESULT_DECL && return_offset >= 0)
                    {
                        result.emplace(function, reinterpret_cast<std::uint8_t*>(reinterpret_cast<std::uintptr_t>(context.RESULT_DECL) - static_cast<std::uintptr_t>(return_offset)));
                        call.result = result->target();
                        call.has_result = true;
                    }

                    callback(call);
                    // `params` and `result` die here: every view of them becomes invalid.
                }
                catch (...)
                {
                    // A failing hook must never unwind into the engine.
                }
            };
        }

        auto function_pointer(const objects::ObjectRef& function) -> Unreal::UFunction*
        {
            game_thread_executor::require_game_thread();
            Unreal::UObject* object = objects::resolve(function);
            if (!object->IsA<Unreal::UFunction>()) throw UEError("this object is not a function");
            return static_cast<Unreal::UFunction*>(object);
        }
    }

    auto register_hook(const objects::ObjectRef& function, HookCallback pre, HookCallback post) -> HookIds
    {
        Unreal::UFunction* target = function_pointer(function);
        const std::ptrdiff_t return_offset = return_offset_of(target);
        const bool no_parameters = target->GetPropertiesSize() == 0;
        try
        {
            const auto [pre_id, post_id] = Unreal::UObjectGlobals::RegisterHook(target, wrap(function, return_offset, no_parameters, std::move(pre)), wrap(function, return_offset, no_parameters, std::move(post)), nullptr);
            return HookIds{pre_id, post_id};
        }
        catch (const std::exception& e)
        {
            throw UEError(std::string("could not hook this function: ") + e.what());
        }
    }

    auto unregister_hook(const objects::ObjectRef& function, HookIds ids) -> void
    {
        Unreal::UFunction* target = function_pointer(function);
        Unreal::UObjectGlobals::UnregisterHook(target, std::pair<int, int>{ids.pre, ids.post});
    }

    namespace
    {
        struct Replacement
        {
            int id{0};
            objects::ObjectRef function{};
            ReplaceCallback callback{};
            bool script{false};              // a Blueprint (script) function: replaced where the VM runs it, not in ProcessEvent
            bool no_parameters{false};       // no parameters and no locals: the VM passes a null frame
            std::ptrdiff_t return_offset{-1};
        };

        std::shared_mutex s_replacements_mutex;
        std::unordered_map<Unreal::UFunction*, std::shared_ptr<Replacement>> s_by_function;
        std::map<int, Unreal::UFunction*> s_by_id;
        std::atomic<int> s_replacement_count{0}; // read on every ProcessEvent: keep the idle path to one load
        std::atomic<int> s_script_replacement_count{0}; // replacements of script functions, read on every local script call
        Unreal::Hook::GlobalCallbackId s_global_callback{Unreal::Hook::ERROR_ID};
        Unreal::Hook::GlobalCallbackId s_local_callback{Unreal::Hook::ERROR_ID};
        int s_next_replacement_id = 1;

        // The function whose replacement is running on this thread: a call of the same function from inside the replacement is
        // the original (this is how a replacement wraps the original instead of recursing).
        thread_local const Unreal::UFunction* t_running = nullptr;

        // Runs on whatever thread calls ProcessEvent, before the original. Nothing may escape.
        auto on_process_event(Unreal::Hook::TCallbackIterationData<void>& data, Unreal::UObject* context, Unreal::UFunction* function, void* params) -> void
        {
            if (s_replacement_count.load(std::memory_order_relaxed) == 0) return;
            std::shared_ptr<Replacement> replacement;
            {
                std::shared_lock lock{s_replacements_mutex};
                const auto it = s_by_function.find(function);
                if (it == s_by_function.end() || it->second->script) return; // script functions are replaced in on_local_script
                replacement = it->second;
            }
            if (!game_thread_executor::on_game_thread()) return; // Python and UE objects belong to the game thread: run the original
            if (t_running == function) return;
            struct Running
            {
                const Unreal::UFunction* previous;
                explicit Running(const Unreal::UFunction* now) : previous(t_running) { t_running = now; }
                ~Running() { t_running = previous; }
            } running(function);
            try
            {
                HookCall call;
                call.context = objects::acquire(context);
                objects::properties::ScopedParams scoped(replacement->function, params);
                call.params = scoped.target();
                if (replacement->callback(call)) data.PreventOriginalFunctionCall();
            }
            catch (...)
            {
                // A failing replacement must never unwind into the engine; the original runs.
            }
        }

        // Runs where the script VM runs a Blueprint function: for a call from Blueprint code and for a ProcessEvent call alike. The
        // parameters are in the frame's locals (out parameters through its OutParms list), the result goes to RESULT_DECL.
        auto on_local_script(Unreal::Hook::TCallbackIterationData<void>& data, Unreal::UObject* context, Unreal::FFrame& stack, void* result_decl) -> void
        {
            if (s_script_replacement_count.load(std::memory_order_relaxed) == 0) return;
            Unreal::UFunction* function = stack.Node();
            std::shared_ptr<Replacement> replacement;
            {
                std::shared_lock lock{s_replacements_mutex};
                const auto it = s_by_function.find(function);
                if (it == s_by_function.end() || !it->second->script) return;
                replacement = it->second;
            }
            if (!game_thread_executor::on_game_thread()) return;
            if (t_running == function) return;
            struct Running
            {
                const Unreal::UFunction* previous;
                explicit Running(const Unreal::UFunction* now) : previous(t_running) { t_running = now; }
                ~Running() { t_running = previous; }
            } running(function);
            try
            {
                void* locals = stack.Locals();
                if (!locals)
                {
                    if (!replacement->no_parameters) return;
                    locals = s_empty_block;
                }
                HookCall call;
                call.context = objects::acquire(context);
                objects::properties::ScopedParams scoped(replacement->function, locals);
                for (Unreal::FOutParmRec* record = stack.OutParms(); record; record = record->NextOutParm)
                {
                    scoped.add_out_parameter(record->Property, record->PropAddr);
                }
                call.params = scoped.target();
                std::optional<objects::properties::ScopedParams> result;
                if (result_decl && replacement->return_offset >= 0)
                {
                    result.emplace(replacement->function, reinterpret_cast<std::uint8_t*>(reinterpret_cast<std::uintptr_t>(result_decl) - static_cast<std::uintptr_t>(replacement->return_offset)));
                    call.result = result->target();
                    call.has_result = true;
                }
                if (replacement->callback(call)) data.PreventOriginalFunctionCall();
            }
            catch (...)
            {
                // A failing replacement must never unwind into the engine; the original runs.
            }
        }

        auto ensure_local_callback() -> void
        {
            if (s_local_callback != Unreal::Hook::ERROR_ID) return;
            if (!Unreal::UObject::ProcessLocalScriptFunctionInternal.is_ready()) throw UEError("this build cannot replace Blueprint functions (the engine's local script function entry was not found)");
            Unreal::Hook::FCallbackOptions options;
            options.OwnerModName = STR("PythonScripting");
            options.HookName = STR("ScriptReplacements");
            s_local_callback = Unreal::Hook::RegisterProcessLocalScriptFunctionPreCallback(on_local_script, options);
            if (s_local_callback == Unreal::Hook::ERROR_ID) throw UEError("could not register the global local script function callback");
        }

        auto ensure_global_callback() -> void
        {
            if (s_global_callback != Unreal::Hook::ERROR_ID) return;
            Unreal::Hook::FCallbackOptions options;
            options.OwnerModName = STR("PythonScripting");
            options.HookName = STR("Replacements");
            s_global_callback = Unreal::Hook::RegisterProcessEventPreCallback(on_process_event, options);
            if (s_global_callback == Unreal::Hook::ERROR_ID) throw UEError("could not register the global ProcessEvent callback");
        }

        auto drop_global_callback_if_unused() -> void
        {
            if (s_by_function.empty() && s_global_callback != Unreal::Hook::ERROR_ID)
            {
                Unreal::Hook::UnregisterCallback(s_global_callback);
                s_global_callback = Unreal::Hook::ERROR_ID;
            }
            if (s_script_replacement_count.load() == 0 && s_local_callback != Unreal::Hook::ERROR_ID)
            {
                Unreal::Hook::UnregisterCallback(s_local_callback);
                s_local_callback = Unreal::Hook::ERROR_ID;
            }
        }
    }

    auto register_replacement(const objects::ObjectRef& function, ReplaceCallback callback) -> int
    {
        Unreal::UFunction* target = function_pointer(function);
        std::unique_lock lock{s_replacements_mutex};
        if (s_by_function.count(target)) throw UEError("this function already has a replacement; unregister it first");
        const bool script = !target->HasAnyFunctionFlags(Unreal::FUNC_Native);
        if (script) ensure_local_callback(); else ensure_global_callback();
        auto replacement = std::make_shared<Replacement>();
        replacement->id = s_next_replacement_id++;
        replacement->function = function;
        replacement->callback = std::move(callback);
        replacement->script = script;
        replacement->no_parameters = target->GetPropertiesSize() == 0;
        replacement->return_offset = return_offset_of(target);
        s_by_function[target] = replacement;
        s_by_id[replacement->id] = target;
        s_replacement_count.store(static_cast<int>(s_by_function.size()), std::memory_order_relaxed);
        if (script) s_script_replacement_count.fetch_add(1);
        return replacement->id;
    }

    auto unregister_replacement(int id) -> bool
    {
        game_thread_executor::require_game_thread();
        std::unique_lock lock{s_replacements_mutex};
        const auto it = s_by_id.find(id);
        if (it == s_by_id.end()) return false;
        const auto found = s_by_function.find(it->second);
        if (found != s_by_function.end() && found->second->script) s_script_replacement_count.fetch_sub(1);
        s_by_function.erase(it->second);
        s_by_id.erase(it);
        s_replacement_count.store(static_cast<int>(s_by_function.size()), std::memory_order_relaxed);
        drop_global_callback_if_unused();
        return true;
    }

    auto unregister_all_replacements() -> void
    {
        std::unique_lock lock{s_replacements_mutex};
        s_by_function.clear();
        s_by_id.clear();
        s_replacement_count.store(0, std::memory_order_relaxed);
        s_script_replacement_count.store(0);
        try
        {
            drop_global_callback_if_unused();
        }
        catch (...)
        {
            // the engine may already be shutting down; the callback finds nothing to do once the table is empty
        }
    }

    auto replacements_active() -> int
    {
        return s_replacement_count.load();
    }
}

