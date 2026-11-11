#include "test.hpp"

#include <pyue/pending_objects.hpp>

#include <atomic>
#include <thread>
#include <vector>

int main()
{
    {
        pyue::PendingObjects pending;
        int a = 0, b = 0;
        PYUE_CHECK(pending.push(1, &a));  // first push asks for a drain
        PYUE_CHECK(!pending.push(2, &b)); // already scheduled
        const auto taken = pending.take();
        PYUE_CHECK(taken.size() == 2);
        PYUE_CHECK(taken[0].index == 1 && taken[0].address == &a);
        PYUE_CHECK(taken[1].index == 2 && taken[1].address == &b);
        PYUE_CHECK(pending.take().empty());
        PYUE_CHECK(pending.push(3, &a)); // after take() the next push schedules again
    }
    {
        // A full list drops and counts, and never asks for a drain by itself.
        pyue::PendingObjects pending(2);
        int x = 0;
        PYUE_CHECK(pending.push(1, &x));
        PYUE_CHECK(!pending.push(2, &x));
        PYUE_CHECK(!pending.push(3, &x));
        PYUE_CHECK(pending.dropped() == 1);
        PYUE_CHECK(pending.take().size() == 2);
        PYUE_CHECK(pending.push(4, &x)); // room again
    }
    {
        pyue::PendingObjects pending;
        int x = 0;
        pending.push(1, &x);
        pending.clear();
        PYUE_CHECK(pending.take().empty());
        PYUE_CHECK(pending.push(2, &x));
    }
    {
        // Many producers, one consumer: nothing lost, and exactly one schedule request per drain.
        pyue::PendingObjects pending;
        std::atomic<int> schedules{0};
        std::vector<std::thread> threads;
        int x = 0;
        for (int t = 0; t < 4; ++t)
        {
            threads.emplace_back([&] {
                for (int i = 0; i < 1000; ++i)
                {
                    if (pending.push(i, &x)) ++schedules;
                }
            });
        }
        for (auto& thread : threads) thread.join();
        PYUE_CHECK(schedules.load() == 1);
        PYUE_CHECK(pending.take().size() == 4000);
    }
    return PYUE_TEST_MAIN_RESULT();
}
