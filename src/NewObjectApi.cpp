#include "NewObjectApi.hpp"

#include <atomic>
#include <memory>
#include <mutex>
#include <vector>

#include <Unreal/UObject.hpp>
#include <Unreal/UObjectArray.hpp>

#include <pyue/errors.hpp>
#include <pyue/pending_objects.hpp>

#include "GameThreadExecutor.hpp"
#include "ObjectApi.hpp"

using namespace RC;

namespace pyue::objects::created
{
    namespace
    {
        struct Subscription
        {
            int id{0};
            ObjectRef cls{};
            Callback callback{};
            std::atomic<bool> removed{false};
        };

        std::mutex s_mutex;
        std::vector<std::shared_ptr<Subscription>> s_subscriptions;
        int s_next_id = 1;
        bool s_listener_installed = false;

        PendingObjects s_pending;
        std::atomic<std::uint64_t> s_announced{0};
        std::atomic<std::uint64_t> s_delivered{0};
        std::atomic<std::uint64_t> s_skipped_dead{0};

        auto drain() -> void;

        // Runs wherever the engine creates an object, inside its construction: it may only record the slot.
        class CreateListener final : public Unreal::FUObjectCreateListener
        {
        public:
            void NotifyUObjectCreated(const Unreal::UObjectBase* object, int32_t index) override
            {
                ++s_announced;
                if (s_pending.push(index, object)) game_thread_executor::post([] { drain(); });
            }

            void OnUObjectArrayShutdown() override
            {
                std::lock_guard lock{s_mutex};
                s_listener_installed = false;
                s_pending.clear();
                Unreal::UObjectArray::RemoveUObjectCreateListener(this);
            }
        };

        CreateListener s_listener{};

        auto drain() -> void
        {
            const auto entries = s_pending.take();
            std::vector<std::shared_ptr<Subscription>> subscriptions;
            {
                std::lock_guard lock{s_mutex};
                subscriptions = s_subscriptions;
            }
            if (subscriptions.empty()) return;

            for (const auto& entry : entries)
            {
                const Unreal::FUObjectItem* item = Unreal::UObjectArray::IndexToObject(entry.index);
                if (!item || item->GetUObject() != entry.address)
                {
                    ++s_skipped_dead;
                    continue;
                }
                ObjectRef ref;
                try
                {
                    ref = acquire(item->GetUObject());
                    if (!is_valid(ref))
                    {
                        ++s_skipped_dead;
                        continue;
                    }
                }
                catch (...)
                {
                    continue;
                }

                for (const auto& subscription : subscriptions)
                {
                    if (subscription->removed.load()) continue;
                    try
                    {
                        if (!api::is_a(ref, subscription->cls)) continue;
                        ++s_delivered;
                        subscription->callback(ref);
                    }
                    catch (...)
                    {
                        // A dead class (is_a throws), or a callback that threw: the callback reports its own errors.
                    }
                }
            }
        }
    }

    auto subscribe(const ObjectRef& cls, Callback callback) -> int
    {
        game_thread_executor::require_game_thread();
        if (api::kind_of(cls) != api::ObjectKind::Class) throw UEError("notify_on_new_object needs a class");

        auto subscription = std::make_shared<Subscription>();
        subscription->cls = cls;
        subscription->callback = std::move(callback);

        std::lock_guard lock{s_mutex};
        subscription->id = s_next_id++;
        s_subscriptions.push_back(subscription);
        if (!s_listener_installed)
        {
            Unreal::UObjectArray::AddUObjectCreateListener(&s_listener);
            s_listener_installed = true;
        }
        return subscription->id;
    }

    auto unsubscribe(int id) -> bool
    {
        game_thread_executor::require_game_thread();
        std::lock_guard lock{s_mutex};
        for (auto it = s_subscriptions.begin(); it != s_subscriptions.end(); ++it)
        {
            if ((*it)->id != id) continue;
            (*it)->removed.store(true);
            s_subscriptions.erase(it);
            if (s_subscriptions.empty() && s_listener_installed)
            {
                Unreal::UObjectArray::RemoveUObjectCreateListener(&s_listener);
                s_listener_installed = false;
                s_pending.clear();
            }
            return true;
        }
        return false;
    }

    auto unsubscribe_all() -> void
    {
        std::lock_guard lock{s_mutex};
        for (const auto& subscription : s_subscriptions) subscription->removed.store(true);
        s_subscriptions.clear();
        if (s_listener_installed)
        {
            try
            {
                Unreal::UObjectArray::RemoveUObjectCreateListener(&s_listener);
            }
            catch (...)
            {
            }
            s_listener_installed = false;
        }
        s_pending.clear();
    }

    auto stats() -> Stats
    {
        return Stats{s_announced.load(), s_delivered.load(), s_pending.dropped(), s_skipped_dead.load()};
    }
}
