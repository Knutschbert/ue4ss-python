#include "test.hpp"

#include <pyue/game_thread.hpp>
#include <pyue/work_queue.hpp>

#include <atomic>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

int main()
{
    {
        // FIFO order.
        pyue::WorkQueue queue;
        std::string order;
        queue.push([&] { order += 'a'; });
        queue.push([&] { order += 'b'; });
        queue.push([&] { order += 'c'; });
        PYUE_CHECK(queue.size() == 3);
        const auto result = queue.drain();
        PYUE_CHECK(result.ran == 3);
        PYUE_CHECK(result.failed == 0);
        PYUE_CHECK(result.remaining == 0);
        PYUE_CHECK(!result.budget_exceeded);
        PYUE_CHECK(order == "abc");
        PYUE_CHECK(queue.drain().ran == 0); // empty queue is fine
    }
    {
        // clear() drops tasks without running them and destroys them outside the lock.
        pyue::WorkQueue queue;
        int runs = 0;
        auto token = std::make_shared<int>(0);
        queue.push([&runs, token] { ++runs; });
        PYUE_CHECK(token.use_count() == 2);
        queue.clear();
        PYUE_CHECK(queue.size() == 0);
        PYUE_CHECK(token.use_count() == 1);
        PYUE_CHECK(queue.drain().ran == 0 && runs == 0);
    }
    {
        // A task that pushes another task: the new one runs in the next drain, not this one.
        pyue::WorkQueue queue;
        int runs = 0;
        queue.push([&] {
            ++runs;
            queue.push([&] { ++runs; });
        });
        auto result = queue.drain();
        PYUE_CHECK(result.ran == 1);
        PYUE_CHECK(result.remaining == 1);
        PYUE_CHECK(runs == 1);
        result = queue.drain();
        PYUE_CHECK(result.ran == 1);
        PYUE_CHECK(runs == 2);
    }
    {
        // A task that re-queues itself forever cannot make a single drain loop forever.
        pyue::WorkQueue queue;
        int runs = 0;
        std::function<void()> again = [&] {
            ++runs;
            queue.push(again);
        };
        queue.push(again);
        queue.drain();
        PYUE_CHECK(runs == 1);
        PYUE_CHECK(queue.size() == 1);
    }
    {
        // Budget, with a fake clock that advances 10 ms per reading.
        pyue::WorkQueue queue;
        int runs = 0;
        for (int i = 0; i < 10; ++i) queue.push([&] { ++runs; });
        auto tick = std::chrono::steady_clock::time_point{};
        const auto fake_now = [&] {
            tick += 10ms;
            return tick;
        };
        const auto result = queue.drain(25ms, fake_now);
        PYUE_CHECK(result.budget_exceeded);
        PYUE_CHECK(result.ran >= 1 && result.ran < 10);
        PYUE_CHECK(static_cast<int>(result.ran) == runs);
        PYUE_CHECK(result.remaining == 10 - result.ran);
        // The rest runs in the next drain, in order.
        const auto rest = queue.drain();
        PYUE_CHECK(rest.ran == result.remaining);
        PYUE_CHECK(runs == 10);
    }
    {
        // At least one task runs even with a tiny budget.
        pyue::WorkQueue queue;
        int runs = 0;
        queue.push([&] { ++runs; });
        queue.push([&] { ++runs; });
        auto tick = std::chrono::steady_clock::time_point{};
        const auto result = queue.drain(1ns, [&] {
            tick += 1s;
            return tick;
        });
        PYUE_CHECK(result.ran == 1);
        PYUE_CHECK(runs == 1);
        PYUE_CHECK(result.budget_exceeded);
    }
    {
        // Throwing tasks are counted and do not stop the drain.
        pyue::WorkQueue queue;
        int runs = 0;
        queue.push([] { throw std::runtime_error("boom"); });
        queue.push([] { throw 7; });
        queue.push([&] { ++runs; });
        const auto result = queue.drain();
        PYUE_CHECK(result.ran == 3);
        PYUE_CHECK(result.failed == 2);
        PYUE_CHECK(runs == 1);
    }
    {
        // Many producers, one consumer: nothing lost, per-producer order preserved.
        pyue::WorkQueue queue;
        constexpr int producers = 4;
        constexpr int per_producer = 5000;
        std::vector<std::vector<int>> seen(producers);
        std::atomic<bool> go{false};

        std::vector<std::thread> threads;
        for (int p = 0; p < producers; ++p)
        {
            threads.emplace_back([&, p] {
                while (!go.load()) std::this_thread::yield();
                for (int i = 0; i < per_producer; ++i) queue.push([&seen, p, i] { seen[p].push_back(i); });
            });
        }
        go = true;

        std::size_t total = 0;
        while (total < static_cast<std::size_t>(producers) * per_producer)
        {
            total += queue.drain().ran;
            std::this_thread::yield();
        }
        for (auto& thread : threads) thread.join();
        total += queue.drain().ran;

        PYUE_CHECK(total == static_cast<std::size_t>(producers) * per_producer);
        for (int p = 0; p < producers; ++p)
        {
            PYUE_CHECK(static_cast<int>(seen[p].size()) == per_producer);
            bool ordered = true;
            for (int i = 0; i < static_cast<int>(seen[p].size()); ++i) ordered = ordered && seen[p][i] == i;
            PYUE_CHECK(ordered);
        }
    }
    {
        // GameThread: unknown until captured, first capture wins, only that thread is "current".
        pyue::GameThread game_thread;
        PYUE_CHECK(!game_thread.is_known());
        PYUE_CHECK(!game_thread.is_current());

        std::thread first([&] {
            PYUE_CHECK(game_thread.capture());
            PYUE_CHECK(game_thread.is_current());
        });
        first.join();

        PYUE_CHECK(game_thread.is_known());
        PYUE_CHECK(!game_thread.is_current()); // this thread is not the captured one
        PYUE_CHECK(!game_thread.capture());    // second capture ignored
        PYUE_CHECK(!game_thread.is_current());

        game_thread.reset();
        PYUE_CHECK(!game_thread.is_known());
        PYUE_CHECK(game_thread.capture());
        PYUE_CHECK(game_thread.is_current());
    }
    return PYUE_TEST_MAIN_RESULT();
}
