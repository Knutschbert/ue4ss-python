#pragma once

#include <cstdint>
#include <mutex>
#include <vector>

namespace pyue
{
    // Identifies one incarnation of an object-array slot. See docs/LIFETIME.md.
    struct Handle
    {
        std::int32_t index{-1};
        std::uint32_t generation{0};
        std::uint32_t epoch{0};

        friend auto operator==(const Handle&, const Handle&) -> bool = default;
    };

    inline auto hash_handle(const Handle& handle) -> std::size_t
    {
        auto x = (static_cast<std::uint64_t>(static_cast<std::uint32_t>(handle.index)) << 32) | handle.generation;
        x ^= static_cast<std::uint64_t>(handle.epoch) * 0x9e3779b97f4a7c15ULL;
        x += 0x9e3779b97f4a7c15ULL;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
        return static_cast<std::size_t>(x ^ (x >> 31));
    }

    // Tracks which handles still refer to a live object, without touching the object itself.
    //
    // The engine frees an object-array slot and may hand the same slot (and often the same address)
    // to a new object. on_deleted() is called from the engine's delete notification and bumps the
    // slot's generation, so every handle created before the deletion stops being current.
    //
    // Thread safety: all members are safe to call from any thread. The mutex is only ever held
    // for a few instructions and never while calling out of this class, so on_deleted() can run
    // inside an engine callback that holds engine locks.
    class HandleTable
    {
    public:
        // Far above any real object array (the engine caps it at about 8M elements), far below an int32
        // overflow. Stops a corrupt index from making the table allocate gigabytes.
        static constexpr std::int32_t max_slots = 64 * 1024 * 1024;

        // Handle for the object currently in `index`. Negative or absurd indices give an invalid handle.
        auto acquire(std::int32_t index) -> Handle
        {
            if (index < 0 || index >= max_slots) return Handle{};
            std::lock_guard lock{m_mutex};
            const auto slot = static_cast<std::size_t>(index);
            if (slot >= m_generations.size()) m_generations.resize(slot + 1, 0);
            return Handle{index, m_generations[slot], m_epoch};
        }

        // The object in `index` was destroyed (or the slot was freed).
        auto on_deleted(std::int32_t index) -> void
        {
            if (index < 0) return;
            std::lock_guard lock{m_mutex};
            const auto slot = static_cast<std::size_t>(index);
            // A slot beyond the table has never had a handle acquired, so nothing can refer to it.
            if (slot < m_generations.size()) ++m_generations[slot];
        }

        // The whole object array is going away: every outstanding handle becomes stale.
        auto shutdown() -> void
        {
            std::lock_guard lock{m_mutex};
            ++m_epoch;
        }

        auto is_current(const Handle& handle) const -> bool
        {
            if (handle.index < 0) return false;
            std::lock_guard lock{m_mutex};
            const auto slot = static_cast<std::size_t>(handle.index);
            return handle.epoch == m_epoch && slot < m_generations.size() && handle.generation == m_generations[slot];
        }

        auto size() const -> std::size_t
        {
            std::lock_guard lock{m_mutex};
            return m_generations.size();
        }

    private:
        mutable std::mutex m_mutex{};
        std::vector<std::uint32_t> m_generations{};
        std::uint32_t m_epoch{0};
    };
}
