#pragma once

#include <chrono>
#include <cstddef>
#include <deque>
#include <exception>
#include <functional>
#include <mutex>
#include <utility>

namespace pyue
{
    struct DrainResult
    {
        std::size_t ran{0};
        std::size_t failed{0};          // tasks that threw
        std::size_t remaining{0};       // queued after this drain (including tasks pushed during it)
        bool budget_exceeded{false};
    };

    // Multi-producer, single-consumer FIFO of tasks. Producers call push() from any thread; the
    // consumer (the game thread, from the engine tick) calls drain(). See docs/THREADING.md.
    class WorkQueue
    {
    public:
        using Task = std::function<void()>;

        auto push(Task task) -> void
        {
            std::lock_guard lock{m_mutex};
            m_tasks.push_back(std::move(task));
        }

        auto size() const -> std::size_t
        {
            std::lock_guard lock{m_mutex};
            return m_tasks.size();
        }

        // Drops queued tasks without running them (their destructors run here, on the calling thread).
        auto clear() -> void
        {
            std::deque<Task> dropped;
            {
                std::lock_guard lock{m_mutex};
                dropped.swap(m_tasks);
            }
        }

        // Runs queued tasks in FIFO order.
        //
        // - Only tasks that were queued when drain() started run in this call; a task that pushes
        //   another task cannot keep the frame busy forever, the new one runs in the next drain.
        // - `budget` is checked between tasks, never inside one, and at least one task always runs
        //   so a tiny budget cannot starve the queue. Pass a zero or negative budget for "unlimited".
        // - A task that throws is counted and does not stop the drain. Tasks are expected to handle
        //   their own errors (the Python wrapper logs tracebacks); this only keeps the engine safe.
        // - The mutex is not held while a task runs, so tasks may push() and size().
        template <typename NowFn>
        auto drain(std::chrono::nanoseconds budget, NowFn&& now) -> DrainResult
        {
            DrainResult result;
            std::size_t snapshot;
            {
                std::lock_guard lock{m_mutex};
                snapshot = m_tasks.size();
            }

            const auto start = now();
            for (std::size_t i = 0; i < snapshot; ++i)
            {
                if (i > 0 && budget.count() > 0 && std::chrono::duration_cast<std::chrono::nanoseconds>(now() - start) >= budget)
                {
                    result.budget_exceeded = true;
                    break;
                }

                Task task;
                {
                    std::lock_guard lock{m_mutex};
                    if (m_tasks.empty()) break;
                    task = std::move(m_tasks.front());
                    m_tasks.pop_front();
                }

                try
                {
                    if (task) task();
                }
                catch (...)
                {
                    ++result.failed;
                }
                ++result.ran;
            }

            result.remaining = size();
            return result;
        }

        auto drain(std::chrono::nanoseconds budget = std::chrono::nanoseconds::zero()) -> DrainResult
        {
            return drain(budget, [] { return std::chrono::steady_clock::now(); });
        }

    private:
        mutable std::mutex m_mutex{};
        std::deque<Task> m_tasks{};
    };
}
