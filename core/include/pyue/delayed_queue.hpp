#pragma once

#include <chrono>
#include <cstddef>
#include <map>
#include <mutex>
#include <utility>

#include <pyue/work_queue.hpp>

namespace pyue
{
    // Tasks that become runnable at a given time. The engine tick calls release_due() just before
    // draining the WorkQueue, so a delay is accurate to one frame (like Lua's ExecuteWithDelay).
    class DelayedQueue
    {
    public:
        using Clock = std::chrono::steady_clock;
        using Task = WorkQueue::Task;

        auto add(Clock::time_point due, Task task) -> void
        {
            std::lock_guard lock{m_mutex};
            m_tasks.emplace(due, std::move(task)); // multimap keeps insertion order for equal keys
        }

        // Moves every task due at `now` or earlier into `out`, earliest first. Returns how many.
        auto release_due(Clock::time_point now, WorkQueue& out) -> std::size_t
        {
            std::size_t released = 0;
            for (;;)
            {
                Task task;
                {
                    std::lock_guard lock{m_mutex};
                    const auto first = m_tasks.begin();
                    if (first == m_tasks.end() || first->first > now) break;
                    task = std::move(first->second);
                    m_tasks.erase(first);
                }
                out.push(std::move(task));
                ++released;
            }
            return released;
        }

        auto size() const -> std::size_t
        {
            std::lock_guard lock{m_mutex};
            return m_tasks.size();
        }

        auto clear() -> void
        {
            std::lock_guard lock{m_mutex};
            m_tasks.clear();
        }

    private:
        mutable std::mutex m_mutex{};
        std::multimap<Clock::time_point, Task> m_tasks{};
    };
}
