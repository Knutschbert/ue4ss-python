#pragma once

#include <cstdint>
#include <string>

#include <pyue/handle_table.hpp>

namespace RC::Unreal
{
    class UObject;
}

// Engine-side registry of object handles (docs/LIFETIME.md). Python-free; includes no Unreal headers
// so it can be used from any translation unit. The cached pointer inside ObjectRef is opaque to
// callers: it is only ever dereferenced after resolve().
namespace pyue::objects
{
    struct ObjectRef
    {
        pyue::Handle handle{};
        RC::Unreal::UObject* object{nullptr};
    };

    struct Stats
    {
        std::uint64_t deleted_total{0};
        std::uint64_t deleted_on_game_thread{0};
        std::uint64_t deleted_off_game_thread{0};
        std::uint64_t deleted_before_game_thread_known{0};
    };

    struct SelfTestResult
    {
        std::wstring object_name{};
        bool resolved{false};
        bool stale_rejected{false};
        bool slot_mismatch_rejected{false};
        bool out_of_range_rejected{false};
        bool null_rejected{false};
        bool destroyed_message_ok{false};
        Stats stats{};
    };

    // Registers the delete listener. Needs Unreal to be initialised. Idempotent.
    auto install() -> bool;
    auto uninstall() -> void;

    // Handle for `object` (null gives an invalid ref). Throws UEError if install() has not run.
    auto acquire(RC::Unreal::UObject* object) -> ObjectRef;

    // Like acquire() for a pointer read out of game memory (a property, an array element): the pointer may be stale, garbage
    // or point into unmapped memory, so it is checked before it is dereferenced. Returns a null ref (so Python sees None)
    // unless the address is exactly the object the global object array holds in the slot the pointer's index names.
    auto acquire_untrusted(RC::Unreal::UObject* object) -> ObjectRef;

    // The live object, or throws InvalidObjectError saying why it is gone.
    auto resolve(const ObjectRef& ref) -> RC::Unreal::UObject*;

    // Non-throwing form of resolve()'s check.
    auto is_valid(const ObjectRef& ref) -> bool;

    auto stats() -> Stats;

    // Checks the registry against a real engine object without disturbing any live handle.
    auto run_self_test() -> SelfTestResult;
}
