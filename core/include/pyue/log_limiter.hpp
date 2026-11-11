#pragma once

#include <cstddef>
#include <map>
#include <mutex>
#include <string>

namespace pyue
{
    // Lets the first few occurrences of a message through, then goes quiet, so a hook that misbehaves on every frame
    // does not flood the log. Thread safe.
    class LogLimiter
    {
    public:
        enum class Decision
        {
            Log,        // print it
            LogAndMute, // print it, and say that further occurrences are muted
            Mute,       // do not print
        };

        explicit LogLimiter(std::size_t allowed_per_key = 3) : m_allowed(allowed_per_key) {}

        auto should_log(const std::string& key) -> Decision
        {
            std::lock_guard lock{m_mutex};
            const std::size_t seen = ++m_counts[key];
            if (seen < m_allowed) return Decision::Log;
            if (seen == m_allowed) return Decision::LogAndMute;
            return Decision::Mute;
        }

        auto count(const std::string& key) const -> std::size_t
        {
            std::lock_guard lock{m_mutex};
            const auto it = m_counts.find(key);
            return it == m_counts.end() ? 0 : it->second;
        }

        auto reset() -> void
        {
            std::lock_guard lock{m_mutex};
            m_counts.clear();
        }

    private:
        std::size_t m_allowed;
        mutable std::mutex m_mutex{};
        std::map<std::string, std::size_t> m_counts{};
    };
}
