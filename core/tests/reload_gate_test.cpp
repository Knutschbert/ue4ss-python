#include "test.hpp"

#include <pyue/reload_gate.hpp>

#include <atomic>
#include <thread>
#include <vector>

int main()
{
    {
        pyue::ReloadGate gate;
        PYUE_CHECK(gate.generation() == 0);
        PYUE_CHECK(gate.start() == 1 && gate.generation() == 1);
        PYUE_CHECK(gate.request());          // schedule it
        PYUE_CHECK(!gate.request());         // already waiting: coalesced
        PYUE_CHECK(!gate.request());
        PYUE_CHECK(gate.pending());
        PYUE_CHECK(gate.begin() == 2);       // the reload starts: generation 2
        PYUE_CHECK(!gate.pending() && gate.generation() == 2);
        PYUE_CHECK(gate.request());          // a request during/after the reload schedules the next one
        PYUE_CHECK(gate.begin() == 3);
    }
    {
        // many threads asking at once: exactly one of them schedules
        pyue::ReloadGate gate;
        gate.start();
        std::atomic<int> scheduled{0};
        std::vector<std::thread> threads;
        for (int i = 0; i < 16; ++i)
        {
            threads.emplace_back([&] {
                if (gate.request()) ++scheduled;
            });
        }
        for (auto& t : threads) t.join();
        PYUE_CHECK(scheduled == 1);
    }
    return 0;
}
