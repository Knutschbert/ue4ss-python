#pragma once

#include <cstdint>
#include <functional>

#include "ObjectRegistry.hpp"

// Notifications about newly created objects. Python-free; includes no Unreal headers.
//
// The engine announces an object from inside its construction, on whatever thread creates it. We only record the slot
// there; the game thread delivers it at a later tick, if the object is still alive then and is an instance of the class
// the subscriber asked for. So callbacks always run on the game thread, shortly after creation (not during it), and an
// object that was destroyed in between is never delivered.
namespace pyue::objects::created
{
    using Callback = std::function<void(const ObjectRef&)>;

    // Game thread. `cls` must be a UClass; instances of it and of its subclasses are delivered (default objects too).
    // Returns an id for unsubscribe().
    auto subscribe(const ObjectRef& cls, Callback callback) -> int;

    // Game thread. False if there is no such subscription.
    auto unsubscribe(int id) -> bool;

    auto unsubscribe_all() -> void;

    struct Stats
    {
        std::uint64_t announced{0};  // creation notices received while subscribed
        std::uint64_t delivered{0};  // callbacks run
        std::uint64_t dropped{0};    // notices lost because too many were pending
        std::uint64_t skipped_dead{0}; // objects already destroyed by the time the game thread looked
    };
    auto stats() -> Stats;
}
