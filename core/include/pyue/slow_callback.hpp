#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <optional>
#include <string>

#include "log_limiter.hpp"

namespace pyue
{
    // Decides whether a Python callback that ran on the game thread took too long to be fine, and what to say about it. The
    // game thread cannot draw a frame while a callback runs, so tens of milliseconds are visible hitches. Free of Unreal and
    // Python; the clock is the caller's. Thread safe.
    class SlowCallbackPolicy
    {
    public:
        static constexpr std::int64_t default_threshold_ms = 50;

        explicit SlowCallbackPolicy(std::int64_t threshold_ms = default_threshold_ms, std::size_t messages_per_label = 3)
            : m_threshold_ms(threshold_ms), m_limiter(messages_per_label)
        {
        }

        // 0 (or less) switches the warnings off.
        auto set_threshold_ms(std::int64_t threshold_ms) -> void { m_threshold_ms.store(threshold_ms < 0 ? 0 : threshold_ms); }
        auto threshold_ms() const -> std::int64_t { return m_threshold_ms.load(); }

        // The text to log for a callback named `label` that ran for `elapsed`, or nothing (fast enough, warnings off, or this label
        // has already been reported often enough). The last message for a label says that more are muted.
        auto check(const std::string& label, std::chrono::nanoseconds elapsed) -> std::optional<std::string>
        {
            const std::int64_t limit = m_threshold_ms.load();
            if (limit <= 0 || elapsed < std::chrono::milliseconds(limit)) return std::nullopt;
            const auto decision = m_limiter.should_log(label);
            if (decision == LogLimiter::Decision::Mute) return std::nullopt;
            const auto milliseconds = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
            std::string text = "ue4ss: slow callback: " + label + " took " + std::to_string(milliseconds) + " ms on the game thread (limit " + std::to_string(limit) + " ms)";
            if (decision == LogLimiter::Decision::LogAndMute) text += "; further reports for it are not logged";
            return text;
        }

        auto reset() -> void { m_limiter.reset(); }

    private:
        std::atomic<std::int64_t> m_threshold_ms;
        LogLimiter m_limiter;
    };
}
