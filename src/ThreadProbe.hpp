#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace pyue::thread_probe
{
    enum class Site : int { Init, Tick, ProcessEvent, StaticConstruct, ObjectCreated, ObjectDeleted, Count };

    struct SiteReport
    {
        const char* name{};
        std::uint64_t calls{};
        std::vector<std::uint32_t> thread_ids; // distinct OS thread ids seen, first one first
    };

    // Lock-free and allocation-free: safe inside engine locks. Init and ObjectDeleted are always
    // recorded; the other sites only between start() and the end of the process.
    auto record(Site site) -> void;

    // Registers the observing callbacks (engine tick, ProcessEvent, StaticConstructObject, object creation).
    auto start() -> bool;

    auto snapshot() -> std::vector<SiteReport>;
    auto current_thread_id() -> std::uint32_t;
    auto game_thread_id() -> std::uint32_t; // 0 until the executor captured it
}
