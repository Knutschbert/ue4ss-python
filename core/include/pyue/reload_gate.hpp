#pragma once

#include <atomic>
#include <cstdint>

namespace pyue
{
    // Bookkeeping for a script reload: coalesces requests (ten reload() calls before the next tick make one reload) and counts
    // generations. Generation 1 is the first run of main.py; every completed reload makes a new one. Any thread may call anything.
    class ReloadGate
    {
    public:
        // True if the caller must schedule the reload; false if one is already waiting to run.
        auto request() -> bool
        {
            return !m_pending.exchange(true);
        }

        // Call when the scheduled reload starts: later requests schedule another one.
        auto begin() -> std::uint32_t
        {
            m_pending.store(false);
            return m_generation.fetch_add(1) + 1;
        }

        auto generation() const -> std::uint32_t
        {
            return m_generation.load();
        }

        auto pending() const -> bool
        {
            return m_pending.load();
        }

        // The first run of main.py (not a reload).
        auto start() -> std::uint32_t
        {
            m_generation.store(1);
            return 1;
        }

    private:
        std::atomic<bool> m_pending{false};
        std::atomic<std::uint32_t> m_generation{0};
    };
}
