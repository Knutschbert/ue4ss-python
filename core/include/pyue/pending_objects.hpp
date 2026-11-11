#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <vector>

namespace pyue
{
    // Objects the engine announced as created, waiting for the game thread to deliver them. The engine calls its creation
    // listener from inside object construction, possibly on a loading thread, so the listener only records the slot here and
    // the game thread looks at the object later (when it is fully built, and only if it is still alive).
    class PendingObjects
    {
    public:
        struct Entry
        {
            std::int32_t index{-1};
            const void* address{nullptr};
        };

        explicit PendingObjects(std::size_t max_pending = 1'000'000) : m_max(max_pending) {}

        // Any thread. Returns true when the caller must schedule a drain: the list was empty and nobody has scheduled one.
        // A full list drops the entry and counts it.
        auto push(std::int32_t index, const void* address) -> bool
        {
            std::lock_guard lock{m_mutex};
            if (m_entries.size() >= m_max)
            {
                ++m_dropped;
                return false;
            }
            m_entries.push_back(Entry{index, address});
            const bool schedule = !m_scheduled;
            m_scheduled = true;
            return schedule;
        }

        // Consumer: everything pending, oldest first. The next push asks for a new drain.
        auto take() -> std::vector<Entry>
        {
            std::lock_guard lock{m_mutex};
            std::vector<Entry> out;
            out.swap(m_entries);
            m_scheduled = false;
            return out;
        }

        // Forget everything pending (nobody is subscribed any more).
        auto clear() -> void
        {
            std::lock_guard lock{m_mutex};
            m_entries.clear();
            m_scheduled = false;
        }

        auto dropped() const -> std::uint64_t
        {
            std::lock_guard lock{m_mutex};
            return m_dropped;
        }

    private:
        mutable std::mutex m_mutex;
        std::vector<Entry> m_entries;
        std::size_t m_max;
        std::uint64_t m_dropped{0};
        bool m_scheduled{false};
    };
}
