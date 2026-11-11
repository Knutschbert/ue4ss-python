#include "DelegateApi.hpp"

#include <atomic>
#include <chrono>
#include <map>
#include <optional>
#include <unordered_map>
#include <unordered_set>

#include <PolyHook2/Detour/x64Detour.hpp>

#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/VersionedContainer/UnrealVirtualImpl/UnrealVirtualBaseVC.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/Hooks/Hooks.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/UClass.hpp>
#include <Unreal/UFunction.hpp>
#include <Unreal/UObject.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/UStruct.hpp>

#include <pyue/errors.hpp>

#include "GameThreadExecutor.hpp"
#include "ObjectApi.hpp"
#include "ObjectRegistry.hpp"
#include "UnrealBridge.hpp"

using namespace RC;

namespace pyue::delegates
{
    namespace
    {
        Unreal::FProperty* find_property(Unreal::UObject* object, std::wstring_view name)
        {
            const auto name_id = bridge::name_from_string(name, false);
            if (!name_id) return nullptr;
            const Unreal::FName wanted(name_id->index, name_id->number);
            Unreal::UClass* cls = object->GetClassPrivate();
            return cls ? cls->GetPropertyByNameInChain(wanted) : nullptr;
        }

        auto multicast_delegate_of(Unreal::UObject* object, Unreal::FProperty* property) -> Unreal::FMulticastScriptDelegate*
        {
            if (!property) return nullptr;
            if (!Unreal::CastField<Unreal::FMulticastInlineDelegateProperty>(property)) return nullptr;
            return reinterpret_cast<Unreal::FMulticastScriptDelegate*>(
                reinterpret_cast<std::uint8_t*>(object) + property->GetOffset_Internal());
        }

        auto signature_of(Unreal::FProperty* property) -> objects::ObjectRef
        {
            auto* multicast = Unreal::CastField<Unreal::FMulticastDelegateProperty>(property);
            if (!multicast) return {};
            auto* function = static_cast<Unreal::UFunction*>(multicast->GetSignatureFunction().Get());
            return function ? objects::acquire(function) : objects::ObjectRef{};
        }

        // --- global broadcast dispatch ---------------------------------------------------------------
        // One global ProcessEvent pre-callback + one local-script pre-callback (same mechanism as register_replacement,
        // proven to catch delegate broadcasts). They dispatch only when `function` is a signature UFunction we bound,
        // multiplexing by the broadcasting object through the core table.

        std::unordered_set<Unreal::UFunction*> s_bound_signatures; // signature UFunctions we currently dispatch
        enum class DispatchRoute
        {
            Unknown,
            ProcessEvent,
            LocalScript,
        };
        std::unordered_map<Unreal::UFunction*, DispatchRoute> s_signature_routes;
        std::mutex s_bound_signatures_mutex;
        std::atomic<int> s_bound_signature_count{0};
        Unreal::Hook::GlobalCallbackId s_global_callback{Unreal::Hook::ERROR_ID};
        Unreal::Hook::GlobalCallbackId s_local_callback{Unreal::Hook::ERROR_ID};
        Dispatch s_dispatch; // set by the Python layer before the first bind_event

        // --- profiling (see BroadcastProfile) ---
        std::atomic<std::uint64_t> s_pe_seen{0};
        std::atomic<std::uint64_t> s_script_seen{0};
        std::atomic<std::uint64_t> s_matched{0};
        std::atomic<std::uint64_t> s_total_ns{0};
        std::atomic<std::uint64_t> s_reset_count{0};

        auto now_ns() -> std::uint64_t
        {
            return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now().time_since_epoch()).count());
        }

        // Runs the Python callbacks that should fire for a broadcast on `context` of `signature` with parameters from
        // `params` (borrowed). Nothing may escape. Mirrors HookApi's wrap/run_python_callback guards.
        auto fire_for(const hooks::HookCall& call, Unreal::UFunction* signature) -> void
        {
            if (s_bound_signature_count.load(std::memory_order_relaxed) == 0) return;
            if (!game_thread_executor::on_game_thread()) return; // delegates broadcast on the game thread
            const std::uint64_t object_key = static_cast<std::uint64_t>(objects::api::address(call.context));
            const std::uint64_t sig_key = reinterpret_cast<std::uintptr_t>(signature);
            const std::vector<int> ids = bindings().active(sig_key, object_key);
            if (ids.empty()) return;
            s_matched.fetch_add(1, std::memory_order_relaxed);
            if (!s_dispatch) return;
            for (const int id : ids)
            {
                try
                {
                    s_dispatch(call, id);
                }
                catch (...)
                {
                    // a failing callback must never unwind into the engine
                }
            }
        }

        // Wildcard diagnostic: when on, record delegate-signature function names (bounded).
        std::atomic<bool> s_broadcast_diag{false};
        std::mutex s_broadcast_diag_mutex;
        std::vector<std::wstring> s_broadcast_diag_names;

        struct ProcessEventDumpCounts
        {
            std::uint64_t process_events{0};
            std::uint64_t script_calls{0};
        };
        constexpr std::size_t max_process_event_dump_names = 8192;
        std::atomic<bool> s_process_event_dump{false};
        std::mutex s_process_event_dump_mutex;
        std::map<std::uintptr_t, ProcessEventDumpCounts> s_process_event_dump_functions;

        auto record_process_event_dump_function(Unreal::UFunction* function, bool script) -> void
        {
            if (!function) return;
            const auto address = reinterpret_cast<std::uintptr_t>(function);
            std::lock_guard lock{s_process_event_dump_mutex};
            auto it = s_process_event_dump_functions.find(address);
            if (it == s_process_event_dump_functions.end())
            {
                if (s_process_event_dump_functions.size() >= max_process_event_dump_names) return;
                it = s_process_event_dump_functions.emplace(address, ProcessEventDumpCounts{}).first;
            }
            if (script) ++it->second.script_calls;
            else ++it->second.process_events;
        }

        // Cheap check: build the full name only when the diag is on, and record it when it is a delegate signature.
        auto record_delegate_broadcast_name(Unreal::UFunction* function) -> void
        {
            const std::wstring full(function->GetFullName());
            if (full.find(STR("__DelegateSignature")) == std::wstring::npos) return;
            std::lock_guard lock{s_broadcast_diag_mutex};
            if (s_broadcast_diag_names.size() < 64) s_broadcast_diag_names.push_back(full);
        }

        auto is_bound_signature(Unreal::UFunction* function) -> bool
        {
            std::lock_guard lock{s_bound_signatures_mutex};
            return s_bound_signatures.find(function) != s_bound_signatures.end();
        }

        auto claim_route(Unreal::UFunction* function, DispatchRoute route) -> bool
        {
            std::lock_guard lock{s_bound_signatures_mutex};
            auto& selected = s_signature_routes[function];
            if (selected == DispatchRoute::Unknown) selected = route;
            return selected == route;
        }

        auto on_process_event(Unreal::Hook::TCallbackIterationData<void>&, Unreal::UObject* context, Unreal::UFunction* function, void* params) -> void
        {
            if (s_process_event_dump.load(std::memory_order_relaxed)) record_process_event_dump_function(function, false);
            if (s_bound_signature_count.load(std::memory_order_relaxed) == 0) return;
            s_pe_seen.fetch_add(1, std::memory_order_relaxed);
            if (s_broadcast_diag.load(std::memory_order_relaxed) && game_thread_executor::on_game_thread())
            {
                record_delegate_broadcast_name(function);
            }
            const std::uint64_t start = now_ns();
            if (!is_bound_signature(function)) { s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed); return; }
            if (!claim_route(function, DispatchRoute::ProcessEvent)) { s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed); return; }
            if (!game_thread_executor::on_game_thread()) { s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed); return; }
            try
            {
                hooks::HookCall call;
                call.context = objects::acquire(context);
                objects::properties::ScopedParams scoped(objects::acquire(function), params);
                call.params = scoped.target();
                fire_for(call, function);
            }
            catch (...)
            {
            }
            s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed);
        }

        auto on_local_script(Unreal::Hook::TCallbackIterationData<void>&, Unreal::UObject* context, Unreal::FFrame& stack, void*) -> void
        {
            if (s_process_event_dump.load(std::memory_order_relaxed)) record_process_event_dump_function(stack.Node(), true);
            if (s_bound_signature_count.load(std::memory_order_relaxed) == 0) return;
            s_script_seen.fetch_add(1, std::memory_order_relaxed);
            if (s_broadcast_diag.load(std::memory_order_relaxed) && game_thread_executor::on_game_thread())
            {
                record_delegate_broadcast_name(stack.Node());
            }
            const std::uint64_t start = now_ns();
            Unreal::UFunction* function = stack.Node();
            if (!is_bound_signature(function)) { s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed); return; }
            if (!claim_route(function, DispatchRoute::LocalScript)) { s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed); return; }
            if (!game_thread_executor::on_game_thread()) { s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed); return; }
            try
            {
                hooks::HookCall call;
                call.context = objects::acquire(context);
                objects::properties::ScopedParams scoped(objects::acquire(function), stack.Locals());
                for (Unreal::FOutParmRec* record = stack.OutParms(); record; record = record->NextOutParm)
                {
                    scoped.add_out_parameter(record->Property, record->PropAddr);
                }
                call.params = scoped.target();
                fire_for(call, function);
            }
            catch (...)
            {
            }
            s_total_ns.fetch_add(now_ns() - start, std::memory_order_relaxed);
        }

        auto ensure_global_callback() -> void
        {
            if (s_global_callback != Unreal::Hook::ERROR_ID) return;
            Unreal::Hook::FCallbackOptions options;
            options.OwnerModName = STR("PythonScripting");
            options.HookName = STR("EventBroadcasts");
            s_global_callback = Unreal::Hook::RegisterProcessEventPreCallback(on_process_event, options);
            if (s_global_callback == Unreal::Hook::ERROR_ID) throw UEError("could not register the global event broadcast callback");
        }

        auto ensure_local_callback() -> void
        {
            if (s_local_callback != Unreal::Hook::ERROR_ID) return;
            if (!Unreal::UObject::ProcessLocalScriptFunctionInternal.is_ready())
            {
                throw UEError("this build cannot dispatch Blueprint delegate broadcasts (the engine's local script function entry was not found)");
            }
            Unreal::Hook::FCallbackOptions options;
            options.OwnerModName = STR("PythonScripting");
            options.HookName = STR("EventBroadcasts");
            s_local_callback = Unreal::Hook::RegisterProcessLocalScriptFunctionPreCallback(on_local_script, options);
            if (s_local_callback == Unreal::Hook::ERROR_ID) throw UEError("could not register the global delegate broadcast callback");
        }

        void drop_callbacks_if_unused()
        {
            if (s_bound_signature_count.load() == 0)
            {
                if (s_global_callback != Unreal::Hook::ERROR_ID)
                {
                    Unreal::Hook::UnregisterCallback(s_global_callback);
                    s_global_callback = Unreal::Hook::ERROR_ID;
                }
                if (s_local_callback != Unreal::Hook::ERROR_ID)
                {
                    Unreal::Hook::UnregisterCallback(s_local_callback);
                    s_local_callback = Unreal::Hook::ERROR_ID;
                }
            }
        }
    }

    auto resolve(const objects::ObjectRef& object, std::wstring_view name) -> Bound
    {
        game_thread_executor::require_game_thread();
        Unreal::UObject* raw = objects::resolve(object);
        Unreal::FProperty* property = find_property(raw, name);
        if (!property)
        {
            std::string narrow;
            for (const wchar_t c : name) narrow.push_back(static_cast<char>(c < 128 ? c : '?'));
            throw PropertyNotFoundError("the object has no property named '" + narrow + "'");
        }
        if (!multicast_delegate_of(raw, property))
        {
            throw UEError("this property is not a multicast delegate (an Event Dispatcher)");
        }
        Bound bound;
        bound.signature = signature_of(property);
        if (bound.signature.handle.index < 0) throw UEError("the multicast delegate has no signature function");
        bound.signature_key = reinterpret_cast<std::uintptr_t>(objects::resolve(bound.signature));
        bound.object_key = static_cast<std::uint64_t>(objects::api::address(object));
        return bound;
    }

    auto add_entry(const objects::ObjectRef& object, const Bound& bound, std::wstring_view name) -> void
    {
        game_thread_executor::require_game_thread();
        Unreal::UObject* raw = objects::resolve(object);
        Unreal::FProperty* property = find_property(raw, name);
        Unreal::FMulticastScriptDelegate* value = multicast_delegate_of(raw, property);
        if (!value) throw UEError("this property is not a multicast delegate (an Event Dispatcher)");
        auto* signature = static_cast<Unreal::UFunction*>(objects::resolve(bound.signature));
        const Unreal::FName signature_name = signature->GetFName();
        for (const auto& entry : value->InvocationList)
        {
            if (entry.GetUObject() == raw && entry.GetFunctionName() == signature_name) return; // idempotent
        }
        Unreal::FScriptDelegate entry;
        entry.BindUFunction(raw, signature_name);
        value->InvocationList.Add(entry);
    }

    auto remove_entry(const objects::ObjectRef& object, const Bound& bound, std::wstring_view name) -> void
    {
        game_thread_executor::require_game_thread();
        Unreal::UObject* raw = objects::resolve(object);
        Unreal::FProperty* property = find_property(raw, name);
        Unreal::FMulticastScriptDelegate* value = multicast_delegate_of(raw, property);
        if (!value) throw UEError("this property is not a multicast delegate (an Event Dispatcher)");
        const Unreal::FName signature_name = static_cast<Unreal::UFunction*>(objects::resolve(bound.signature))->GetFName();
        for (int32_t i = value->InvocationList.Num() - 1; i >= 0; --i)
        {
            if (value->InvocationList[i].GetUObject() == raw && value->InvocationList[i].GetFunctionName() == signature_name)
            {
                value->InvocationList.RemoveAt(i, 1);
                return;
            }
        }
    }

    auto is_multicast(const objects::ObjectRef& object, std::wstring_view name) -> bool
    {
        try
        {
            (void)resolve(object, name);
            return true;
        }
        catch (...)
        {
            return false;
        }
    }

    auto bindings() -> pyue::DelegateBindings&
    {
        static pyue::DelegateBindings s_bindings;
        return s_bindings;
    }

    auto set_broadcast_dispatcher(Dispatch dispatch) -> void
    {
        s_dispatch = std::move(dispatch);
    }

    auto reset_broadcast() -> void
    {
        s_dispatch = {};
        {
            std::lock_guard lock{s_bound_signatures_mutex};
            s_bound_signatures.clear();
            s_signature_routes.clear();
        }
        s_bound_signature_count.store(0);
        bindings().clear();
        drop_callbacks_if_unused();
    }

    auto active_signature_count() -> std::size_t
    {
        std::lock_guard lock{s_bound_signatures_mutex};
        return s_bound_signatures.size();
    }

    // Called by the Python layer after bindings().add says needs_hook: remember the signature and install both observation
    // routes. The first actual callback path observed for each signature wins; the other route is suppressed so a single
    // broadcast cannot call Python twice, without relying on unreliable flags for generated signatures.
    auto note_signature(const objects::ObjectRef& signature, bool script) -> void
    {
        game_thread_executor::require_game_thread();
        (void)script;
        (void)static_cast<Unreal::UFunction*>(objects::resolve(signature));
        ensure_local_callback();
        ensure_global_callback();
        auto* sig = static_cast<Unreal::UFunction*>(objects::resolve(signature));
        bool inserted = false;
        {
            std::lock_guard lock{s_bound_signatures_mutex};
            inserted = s_bound_signatures.insert(sig).second;
        }
        if (inserted)
        {
            s_bound_signature_count.fetch_add(1);
        }
    }

    // Called by the Python layer after bindings().remove says last_for_signature: stop dispatching that signature.
    auto forget_signature(const objects::ObjectRef& signature) -> void
    {
        game_thread_executor::require_game_thread();
        Unreal::UFunction* sig = nullptr;
        try
        {
            sig = static_cast<Unreal::UFunction*>(objects::resolve(signature));
        }
        catch (...)
        {
            sig = nullptr;
        }
        bool erased = false;
        if (sig)
        {
            std::lock_guard lock{s_bound_signatures_mutex};
            erased = s_bound_signatures.erase(sig) != 0;
            s_signature_routes.erase(sig);
        }
        if (erased)
        {
            s_bound_signature_count.fetch_sub(1);
        }
        drop_callbacks_if_unused();
    }

    auto broadcast_profile() -> BroadcastProfile
    {
        BroadcastProfile profile;
        profile.process_events_seen = s_pe_seen.load(std::memory_order_relaxed);
        profile.script_calls_seen = s_script_seen.load(std::memory_order_relaxed);
        profile.matched = s_matched.load(std::memory_order_relaxed);
        profile.total_ns = s_total_ns.load(std::memory_order_relaxed);
        profile.reset_count = s_reset_count.load(std::memory_order_relaxed);
        return profile;
    }

    auto reset_broadcast_profile() -> void
    {
        s_pe_seen.store(0);
        s_script_seen.store(0);
        s_matched.store(0);
        s_total_ns.store(0);
        s_reset_count.fetch_add(1);
    }

    // --- manual-detour self test ---------------------------------------------------------------
    // Installs a PLH x64Detour on UObject::ProcessEvent with a plain C++ counter callback and NO UEPseudo dispatch
    // machinery, to answer: does a detour at this address fire at all in this game? If the counter increases while the
    // UEPseudo pre-callbacks (process_events_seen) stay 0, the problem is UEPseudo's dispatch, not the address. If the
    // counter stays 0 too, the game never calls through this address (or the bytes are reverted after install).
    namespace
    {
        std::atomic<std::uint64_t> s_manual_pe_calls{0};
        std::atomic<std::uint64_t> s_manual_pe_installed{0};
        PLH::x64Detour* s_manual_pe_detour = nullptr;
        std::uint64_t s_manual_pe_trampoline = 0;

        // The ProcessEvent signature: (UObject* context, UFunction* function, void* params) -> void.
        void manual_pe_callback(Unreal::UObject* context, Unreal::UFunction* function, void* params)
        {
            s_manual_pe_calls.fetch_add(1, std::memory_order_relaxed);
            using OriginalFn = void (*)(Unreal::UObject*, Unreal::UFunction*, void*);
            auto original = reinterpret_cast<OriginalFn>(s_manual_pe_trampoline);
            if (original) original(context, function, params);
        }
    }

    auto install_manual_pe_detour() -> bool
    {
        game_thread_executor::require_game_thread();
        if (s_manual_pe_installed.load()) return true;
        // The vtable macro needs a live UObject instance; UE4SS itself uses the CoreUObject.Default__Object CDO for this
        // (deps/first/Unreal/src/UnrealInitializer.cpp:1110).
        auto* instance = Unreal::UObjectGlobals::StaticFindObject_InternalSlow(nullptr, nullptr, STR("/Script/CoreUObject.Default__Object"));
        if (!instance) return false;
        // Resolve the address ourselves via the UObject vtable (the same address UE4SS logs and UnchainedPlugin's
        // pattern scan agrees with, module offset 0x1FEA930), so the test does not depend on the settings flag.
        auto address = OPTIONAL_GET_ADDRESS_OF_UNREAL_VIRTUAL(Unreal::UObject, ProcessEvent, instance);
        if (!address) return false;
        s_manual_pe_trampoline = std::bit_cast<std::uint64_t>(address);
        s_manual_pe_detour = new PLH::x64Detour(
            std::bit_cast<std::uint64_t>(address),
            std::bit_cast<std::uint64_t>(&manual_pe_callback),
            &s_manual_pe_trampoline);
        if (!s_manual_pe_detour->hook()) return false;
        s_manual_pe_installed.store(1);
        return true;
    }

    auto manual_pe_detour_calls() -> std::uint64_t
    {
        return s_manual_pe_calls.load(std::memory_order_relaxed);
    }

    auto remove_manual_pe_detour() -> void
    {
        if (!s_manual_pe_installed.load()) return;
        if (s_manual_pe_detour)
        {
            (void)s_manual_pe_detour->unHook();
            delete s_manual_pe_detour;
            s_manual_pe_detour = nullptr;
        }
        s_manual_pe_installed.store(0);
    }

    auto set_delegate_broadcast_diag(bool on) -> void
    {
        s_broadcast_diag.store(on);
        if (!on)
        {
            std::lock_guard lock{s_broadcast_diag_mutex};
            s_broadcast_diag_names.clear();
        }
    }

    auto recorded_delegate_broadcasts() -> std::vector<std::wstring>
    {
        std::lock_guard lock{s_broadcast_diag_mutex};
        return s_broadcast_diag_names;
    }

    auto set_process_event_dump(bool on) -> void
    {
        if (on)
        {
            game_thread_executor::require_game_thread();
            ensure_local_callback();
            ensure_global_callback();
            std::lock_guard lock{s_process_event_dump_mutex};
            s_process_event_dump_functions.clear();
        }
        s_process_event_dump.store(on, std::memory_order_relaxed);
    }

    auto recorded_process_event_dump() -> std::vector<ProcessEventDumpEntry>
    {
        std::vector<ProcessEventDumpEntry> result;
        std::lock_guard lock{s_process_event_dump_mutex};
        result.reserve(s_process_event_dump_functions.size());
        for (const auto& [address, counts] : s_process_event_dump_functions)
        {
            auto ref = objects::acquire_untrusted(reinterpret_cast<Unreal::UObject*>(address));
            if (ref.handle.index < 0) continue;
            auto* function = static_cast<Unreal::UFunction*>(objects::resolve(ref));
            result.push_back(ProcessEventDumpEntry{std::wstring(function->GetFullName()), counts.process_events, counts.script_calls});
        }
        return result;
    }
}
