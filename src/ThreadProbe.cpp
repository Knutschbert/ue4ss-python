#include "ThreadProbe.hpp"

#include <array>
#include <atomic>
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>

#include <Unreal/Hooks/Hooks.hpp>
#include <Unreal/UObjectArray.hpp>

#include "GameThreadExecutor.hpp"

using namespace RC;

namespace pyue::thread_probe
{
    namespace
    {
        constexpr int site_count = static_cast<int>(Site::Count);
        constexpr int max_ids = 8;
        constexpr const char* site_names[site_count] = {"init", "tick", "process_event", "static_construct", "object_created", "object_deleted"};

        struct Slot
        {
            std::atomic<std::uint64_t> calls{0};
            std::array<std::atomic<std::uint32_t>, max_ids> ids{};
        };

        Slot s_slots[site_count];
        std::atomic<bool> s_started{false};

        class CreateListener final : public Unreal::FUObjectCreateListener
        {
        public:
            void NotifyUObjectCreated(const Unreal::UObjectBase*, int32_t) override { record(Site::ObjectCreated); }
            void OnUObjectArrayShutdown() override { Unreal::UObjectArray::RemoveUObjectCreateListener(this); }
        };

        CreateListener s_create_listener{};
    }

    auto current_thread_id() -> std::uint32_t { return ::GetCurrentThreadId(); }

    auto game_thread_id() -> std::uint32_t { return game_thread_executor::game_thread_os_id(); }

    auto record(Site site) -> void
    {
        Slot& slot = s_slots[static_cast<int>(site)];
        ++slot.calls;
        const std::uint32_t me = current_thread_id();
        for (auto& id : slot.ids)
        {
            std::uint32_t seen = id.load(std::memory_order_acquire);
            if (seen == me) return;
            if (seen == 0 && id.compare_exchange_strong(seen, me)) return;
            if (seen == me) return; // another thread just stored our id
        }
    }

    auto start() -> bool
    {
        if (s_started.exchange(true)) return true;
        Unreal::Hook::FCallbackOptions options;
        options.OwnerModName = STR("PythonScripting");
        options.HookName = STR("ThreadProbe");
        Unreal::Hook::RegisterEngineTickPreCallback([](auto&, ...) { record(Site::Tick); }, options);
        Unreal::Hook::RegisterProcessEventPreCallback([](auto&, ...) { record(Site::ProcessEvent); }, options);
        Unreal::Hook::RegisterStaticConstructObjectPostCallback([](auto&, ...) { record(Site::StaticConstruct); }, options);
        Unreal::UObjectArray::AddUObjectCreateListener(&s_create_listener);
        return true;
    }

    auto snapshot() -> std::vector<SiteReport>
    {
        std::vector<SiteReport> out;
        for (int i = 0; i < site_count; ++i)
        {
            SiteReport report{site_names[i], s_slots[i].calls.load(), {}};
            for (const auto& id : s_slots[i].ids)
            {
                if (const std::uint32_t v = id.load()) report.thread_ids.push_back(v);
            }
            out.push_back(std::move(report));
        }
        return out;
    }
}
