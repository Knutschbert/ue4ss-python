#pragma once

#include <atomic>
#include <thread>

namespace pyue
{
    // Remembers which thread is the game thread. The id is captured by the first engine-tick
    // callback, the same trick UE4SS uses for its GameThreadInitializer (docs/THREADING.md).
    class GameThread
    {
    public:
        // First call wins; later calls from other threads are ignored. Returns true if this call set it.
        auto capture() -> bool
        {
            std::thread::id expected{};
            return m_id.compare_exchange_strong(expected, std::this_thread::get_id());
        }

        auto is_known() const -> bool
        {
            return m_id.load() != std::thread::id{};
        }

        // False until the game thread is known: nothing counts as "on the game thread" before that.
        auto is_current() const -> bool
        {
            const auto id = m_id.load();
            return id != std::thread::id{} && id == std::this_thread::get_id();
        }

        auto id() const -> std::thread::id
        {
            return m_id.load();
        }

        auto reset() -> void
        {
            m_id.store(std::thread::id{});
        }

    private:
        std::atomic<std::thread::id> m_id{};
    };
}
