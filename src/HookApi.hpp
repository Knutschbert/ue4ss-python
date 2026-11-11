#pragma once

#include <functional>

#include "ObjectRegistry.hpp"
#include "PropertyApi.hpp"

// Hooks on engine functions. Python-free; includes no Unreal headers.
namespace pyue::hooks
{
    // What a hook callback is given. `params` is borrowed from the running call and is dead as soon as the callback
    // returns (see properties::ScopedParams).
    struct HookCall
    {
        objects::ObjectRef context{};          // the object the function runs on
        objects::properties::Target params{};  // the function's parameters, laid out like its parameter block
        // The function's result as the engine wrote it (RESULT_DECL), for post-hooks. It is NOT inside `params`: reading
        // params.ReturnValue in a post-hook gives stale data. Laid out like a parameter block whose only meaningful
        // field is the return parameter.
        objects::properties::Target result{};
        bool has_result{false};
    };

    using HookCallback = std::function<void(const HookCall&)>;

    struct HookIds
    {
        int pre{-1};
        int post{-1};
    };

    // Runs `pre` before and `post` after every call of `function`, native or Blueprint. Either may be empty. Needs the
    // game thread. Throws UEError if the engine cannot hook that kind of function. The callbacks run wherever the
    // function runs, so they must check the thread themselves.
    auto register_hook(const objects::ObjectRef& function, HookCallback pre, HookCallback post) -> HookIds;

    auto unregister_hook(const objects::ObjectRef& function, HookIds ids) -> void;

    // --- replacements -------------------------------------------------------------------------------------------------------
    // A replacement runs INSTEAD of a function. The callback gets the call (context and parameter block, like a pre-hook) and
    // returns true to skip the original, after writing any result into the call's return parameter, or false to let the original
    // run as if nothing was registered.
    //
    // Reach: a native function is replaced where it is called through UObject::ProcessEvent (calls made by C++ code, delegates, events,
    // timers, Python calls); a Blueprint script calling a native function does not pass through ProcessEvent and is not seen. A
    // Blueprint (script) function is replaced where the script VM runs it, so Blueprint code calling it is seen as well as ProcessEvent
    // calls; its result is `HookCall::result` (the return parameter) or its only output parameter in `params`. At most one replacement
    // per function. The callback runs on the game thread only; a call from another thread always runs the original.
    using ReplaceCallback = std::function<bool(const HookCall&)>;

    // Needs the game thread. Throws UEError if the function is already replaced or cannot be.
    auto register_replacement(const objects::ObjectRef& function, ReplaceCallback callback) -> int;

    // False if there is no replacement with that id.
    auto unregister_replacement(int id) -> bool;
    auto unregister_all_replacements() -> void;
    auto replacements_active() -> int;
}

