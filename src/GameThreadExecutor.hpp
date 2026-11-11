#pragma once

#include <chrono>

#include <pyue/work_queue.hpp>

// Runs queued tasks on the game thread, drained from the engine tick. See docs/THREADING.md.
// Free of Python headers on purpose.
namespace pyue::game_thread_executor
{
    enum class Method
    {
        None,        // no hook could be registered: post() would never run
        EngineTick,  // UEngine::Tick post callback (preferred)
        ProcessEvent // fallback when the tick AOB was not found
    };

    // Registers the drain callback. Idempotent. Call after Unreal has initialised.
    auto install() -> Method;

    // Stops draining and removes the callback. Queued tasks are dropped.
    auto uninstall() -> void;

    // Drops every queued and delayed task without running it (a script reload). Tasks that are running right now are not affected.
    auto clear_pending() -> void;

    // Any thread. The task runs on the game thread at a later tick.
    auto post(pyue::WorkQueue::Task task) -> void;

    // Any thread. The task runs on the game thread at the first tick at least `delay` from now.
    auto post_delayed(pyue::WorkQueue::Task task, std::chrono::milliseconds delay) -> void;

    // True once the first drain callback has captured the game thread.
    auto game_thread_known() -> bool;

    // Windows thread id of the game thread, 0 until known. For diagnostics only.
    auto game_thread_os_id() -> unsigned long;

    // True only on the thread that ran the first drain callback.
    auto on_game_thread() -> bool;

    // Throws UEError unless called on the game thread. Every call that touches Unreal objects starts with it.
    auto require_game_thread() -> void;

    auto method() -> Method;
}
