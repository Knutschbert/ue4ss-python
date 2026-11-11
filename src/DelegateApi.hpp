#pragma once

#include <functional>
#include <string>
#include <string_view>
#include <vector>

#include <pyue/delegate_bindings.hpp>

#include "HookApi.hpp"
#include "ObjectRegistry.hpp"

// Binding a Python callback to a multicast delegate (event dispatcher) property on an object.
// Python-free; the .cpp includes the Unreal headers.
//
// Engine mechanism (docs/BLUEPRINT_HOOKS.md, verified from the 4.25 engine source):
// FMulticastScriptDelegate::Broadcast iterates the OWNING object's InvocationList and for each bound entry calls
// ProcessDelegate -> FindFunctionChecked(FunctionName) -> ObjectPtr->ProcessEvent(SignatureFunction, Parameters). So
// adding an FScriptDelegate{object, sigName} to the property's InvocationList makes a broadcast of that delegate run the
// signature UFunction through ProcessEvent (or the script VM) on exactly that object.
//
// We therefore install ONE global ProcessEvent pre-callback and one local-script pre-callback (the same global-callback
// mechanism register_replacement uses, which is proven to catch delegate broadcasts). They dispatch by (signature
// function, object): the core DelegateBindings table says which Python binding ids should run for that pair. Because the
// broadcast walks only the owning object's InvocationList, one global callback is correct for any number of actors: the
// object key in the table multiplexes them.
namespace pyue::delegates
{
    // The outcome of binding to a multicast delegate property: enough to tear the binding down later.
    struct Bound
    {
        objects::ObjectRef signature{}; // the property's __DelegateSignature UFunction
        std::uint64_t signature_key{0}; // key into the core table (address of the signature UFunction)
        std::uint64_t object_key{0};    // key into the core table (address of the owning object)
    };

    // Resolves a multicast delegate property on an object without binding anything. Throws PropertyNotFoundError if
    // there is no property of that name, UEError if it is not a multicast delegate.
    auto resolve(const objects::ObjectRef& object, std::wstring_view name) -> Bound;

    // Appends an FScriptDelegate{object, sigName} to the multicast delegate's InvocationList, so a broadcast reaches the
    // signature UFunction on `object`. Callers only call this once per (signature, object) pair (the core table's
    // needs_entry). Returns without change if the entry is already present.
    auto add_entry(const objects::ObjectRef& object, const Bound& bound, std::wstring_view name) -> void;

    // Removes the {object, sigName} entry that add_entry appended, if it is still there.
    auto remove_entry(const objects::ObjectRef& object, const Bound& bound, std::wstring_view name) -> void;

    // Non-throwing: whether the object's class chain has `name` as a multicast delegate property.
    auto is_multicast(const objects::ObjectRef& object, std::wstring_view name) -> bool;

    // The core registry that maps (signature, object) to the python binding ids that should run on a broadcast. The
    // caller (the Python layer) adds a binding id for each bind_event and reads active() from the broadcast dispatcher.
    auto bindings() -> pyue::DelegateBindings&;

    // What the broadcast dispatcher gives one calling Python binding: the object whose delegate fired, its signature
    // function, and the delegate's parameter block (borrowed; dies when the dispatcher callback returns).
    using Dispatch = std::function<void(const hooks::HookCall& call, int binding_id)>;

    // Installs the global ProcessEvent / local-script pre-callbacks (once) so that every broadcast of a bound delegate
    // signature reaches `dispatch` with each active binding id. `dispatch` must be set before the first bind_event.
    auto set_broadcast_dispatcher(Dispatch dispatch) -> void;

    // Removes any broadcast dispatcher and forgets the bound-signature set (best effort; called on shutdown/reload).
    auto reset_broadcast() -> void;

    // Number of distinct signature functions we are currently dispatching (for shutdown bookkeeping).
    auto active_signature_count() -> std::size_t;

    // After bindings().add says needs_hook (= first binding of this signature anywhere): remember the signature so the
    // global callbacks dispatch it, and ensure the global ProcessEvent/local-script callbacks are installed. `script`
    // says whether the signature runs through the script VM (a Blueprint event dispatcher) or ProcessEvent (a native
    // C++ multicast delegate). One global callback total serves any number of actors (the object key multiplexes).
    auto note_signature(const objects::ObjectRef& signature, bool script) -> void;

    // After bindings().remove says last_for_signature: stop dispatching that signature function.
    auto forget_signature(const objects::ObjectRef& signature) -> void;

    // Profiling: how much the global broadcast callbacks cost. The callbacks run on EVERY ProcessEvent / script call
    // while any signature is bound, so these counters show per-frame overhead. All reads are cheap atomics.
    struct BroadcastProfile
    {
        std::uint64_t process_events_seen{0};    // ProcessEvent calls that reached our global callback
        std::uint64_t script_calls_seen{0};      // script-VM calls that reached our global callback
        std::uint64_t matched{0};                // of those, how many were a bound signature we dispatched
        std::uint64_t total_ns{0};               // cumulative time inside the callbacks (dispatch included)
        std::uint64_t reset_count{0};
    };
    auto broadcast_profile() -> BroadcastProfile;
    auto reset_broadcast_profile() -> void;

    // Manual-detour self test: a PLH x64Detour on UObject::ProcessEvent with a plain counter
    // callback and no UEPseudo dispatch. install calls the original through the trampoline; needs the game thread.
    auto install_manual_pe_detour() -> bool;
    auto manual_pe_detour_calls() -> std::uint64_t;
    auto remove_manual_pe_detour() -> void;

    // Wildcard diagnostic: when on, record the full names of any delegate-signature function
    // (name containing __DelegateSignature) passing through the global channels, so we can see the actual naming
    // scheme of delegate broadcasts and whether our bound signature is among them. Off costs one atomic load.
    auto set_delegate_broadcast_diag(bool on) -> void;
    auto recorded_delegate_broadcasts() -> std::vector<std::wstring>;

    // Discovery diagnostic: record unique full function names and counts from the global ProcessEvent and local-script
    // callbacks. Names are read only on the game thread; the bounded unique inventory avoids logging millions of calls.
    struct ProcessEventDumpEntry
    {
        std::wstring name{};
        std::uint64_t process_events{0};
        std::uint64_t script_calls{0};
    };
    auto set_process_event_dump(bool on) -> void;
    auto recorded_process_event_dump() -> std::vector<ProcessEventDumpEntry>;
}
