#include "GameThreadExecutor.hpp"

#include <atomic>
#include <chrono>
#include <string_view>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>

#include <DynamicOutput/DynamicOutput.hpp>
#include <UE4SSRuntime.hpp>
#include <Unreal/Hooks/Hooks.hpp>

#include <pyue/delayed_queue.hpp>
#include <pyue/errors.hpp>
#include <pyue/game_thread.hpp>

using namespace RC;

namespace pyue::game_thread_executor
{
    namespace
    {
        // Callbacks registered with the engine outlive the mod object, so the state is static
        // and the callbacks check s_active before touching it.
        pyue::WorkQueue s_queue{};
        pyue::DelayedQueue s_delayed{};
        pyue::GameThread s_game_thread{};
        std::atomic<bool> s_active{false};
        std::atomic<bool> s_logged_first_drain{false};
        Method s_method{Method::None};
        Unreal::Hook::GlobalCallbackId s_callback_id{Unreal::Hook::ERROR_ID};

        constexpr std::chrono::milliseconds drain_budget{4};

        std::atomic<unsigned long> s_game_thread_os_id{0};

        void on_engine_callback()
        {
            if (!s_active.load(std::memory_order_acquire)) return;

            if (s_game_thread.capture())
            {
                s_game_thread_os_id.store(::GetCurrentThreadId());
                Output::send<LogLevel::Verbose>(STR("PythonScripting: game thread captured via {}\n"),
                                                s_method == Method::EngineTick ? STR("EngineTick") : STR("ProcessEvent (fallback, unverified)"));
            }

            s_delayed.release_due(pyue::DelayedQueue::Clock::now(), s_queue);
            const auto result = s_queue.drain(drain_budget);
            if (result.ran > 0 && !s_logged_first_drain.exchange(true))
            {
                Output::send<LogLevel::Verbose>(STR("PythonScripting: first game-thread drain ran {} task(s), {} failed\n"), result.ran, result.failed);
            }
            if (result.budget_exceeded)
            {
                Output::send<LogLevel::Warning>(STR("PythonScripting: game-thread drain stopped at its {} ms budget, {} task(s) left\n"),
                                                static_cast<long long>(drain_budget.count()),
                                                result.remaining);
            }
        }
    }

    auto install() -> Method
    {
        if (s_active.load()) return s_method;

        Unreal::Hook::FCallbackOptions options;
        options.OwnerModName = STR("PythonScripting");
        options.HookName = STR("GameThreadExecutor");

        // Debug switch (ft9.11): PYUE_EXECUTOR=processevent takes the ProcessEvent path even when the tick hook exists,
        // to test the fallback.
        wchar_t forced[32] = {};
        const bool force_process_event = ::GetEnvironmentVariableW(L"PYUE_EXECUTOR", forced, 32) > 0 && std::wstring_view(forced) == L"processevent";
        if (UE4SSRuntime::IsEngineTickAvailable() && !force_process_event)
        {
            s_callback_id = Unreal::Hook::RegisterEngineTickPostCallback([](auto&, ...) { on_engine_callback(); }, options);
            s_method = Method::EngineTick;
        }
        else if (UE4SSRuntime::IsProcessEventAvailable())
        {
            s_callback_id = Unreal::Hook::RegisterProcessEventPostCallback([](auto&, ...) { on_engine_callback(); }, options);
            s_method = Method::ProcessEvent;
        }
        else
        {
            s_method = Method::None;
            return s_method;
        }

        if (s_callback_id == Unreal::Hook::ERROR_ID)
        {
            s_method = Method::None;
            return s_method;
        }

        s_active.store(true, std::memory_order_release);
        return s_method;
    }

    auto uninstall() -> void
    {
        if (!s_active.exchange(false)) return;
        try
        {
            Unreal::Hook::UnregisterCallback(s_callback_id);
        }
        catch (...)
        {
            // The engine may already be shutting down; the callback is inert now (s_active is false).
        }
        s_callback_id = Unreal::Hook::ERROR_ID;
        // Destroy pending tasks now, while Python is still alive, so their references are released properly.
        s_delayed.clear();
        s_queue.clear();
    }

    auto clear_pending() -> void
    {
        s_queue.clear();
        s_delayed.clear();
    }

    auto post(pyue::WorkQueue::Task task) -> void
    {
        s_queue.push(std::move(task));
    }

    auto post_delayed(pyue::WorkQueue::Task task, std::chrono::milliseconds delay) -> void
    {
        s_delayed.add(pyue::DelayedQueue::Clock::now() + delay, std::move(task));
    }

    auto game_thread_os_id() -> unsigned long
    {
        return s_game_thread_os_id.load();
    }

    auto game_thread_known() -> bool
    {
        return s_game_thread.is_known();
    }

    auto on_game_thread() -> bool
    {
        return s_game_thread.is_current();
    }

    auto require_game_thread() -> void
    {
        if (!s_game_thread.is_current())
        {
            throw pyue::UEError("this must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread");
        }
    }

    auto method() -> Method
    {
        return s_method;
    }
}
