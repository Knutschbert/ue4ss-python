#include "test.hpp"

#include <pyue/delayed_queue.hpp>

#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
using Clock = pyue::DelayedQueue::Clock;

int main()
{
    const auto t0 = Clock::time_point{} + 1000s;

    {
        // Nothing is released before its time; due tasks come out earliest first.
        pyue::DelayedQueue delayed;
        pyue::WorkQueue ready;
        std::string order;
        delayed.add(t0 + 300ms, [&] { order += 'c'; });
        delayed.add(t0 + 100ms, [&] { order += 'a'; });
        delayed.add(t0 + 200ms, [&] { order += 'b'; });

        PYUE_CHECK(delayed.release_due(t0 + 50ms, ready) == 0);
        PYUE_CHECK(ready.size() == 0);
        PYUE_CHECK(delayed.release_due(t0 + 250ms, ready) == 2);
        PYUE_CHECK(delayed.size() == 1);
        ready.drain();
        PYUE_CHECK(order == "ab");
        PYUE_CHECK(delayed.release_due(t0 + 300ms, ready) == 1); // exactly due counts
        ready.drain();
        PYUE_CHECK(order == "abc");
        PYUE_CHECK(delayed.size() == 0);
    }
    {
        // Equal due times keep insertion order.
        pyue::DelayedQueue delayed;
        pyue::WorkQueue ready;
        std::string order;
        for (const char c : std::string("xyz")) delayed.add(t0, [&order, c] { order += c; });
        delayed.release_due(t0, ready);
        ready.drain();
        PYUE_CHECK(order == "xyz");
    }
    {
        // A zero or past delay is released at the next call.
        pyue::DelayedQueue delayed;
        pyue::WorkQueue ready;
        delayed.add(t0 - 5s, [] {});
        PYUE_CHECK(delayed.release_due(t0, ready) == 1);
    }
    {
        // clear() drops pending tasks.
        pyue::DelayedQueue delayed;
        pyue::WorkQueue ready;
        delayed.add(t0, [] {});
        delayed.clear();
        PYUE_CHECK(delayed.release_due(t0 + 1h, ready) == 0);
    }
    {
        // Producers adding while the consumer releases.
        pyue::DelayedQueue delayed;
        pyue::WorkQueue ready;
        std::vector<std::thread> threads;
        for (int p = 0; p < 4; ++p)
        {
            threads.emplace_back([&delayed, t0] {
                for (int i = 0; i < 2000; ++i) delayed.add(t0 + std::chrono::milliseconds(i % 50), [] {});
            });
        }
        std::size_t released = 0;
        for (int i = 0; i < 100; ++i) released += delayed.release_due(t0 + 25ms, ready);
        for (auto& thread : threads) thread.join();
        released += delayed.release_due(t0 + 1h, ready);
        PYUE_CHECK(released == 8000);
        PYUE_CHECK(delayed.size() == 0);
        PYUE_CHECK(ready.size() == 8000);
    }
    return PYUE_TEST_MAIN_RESULT();
}
