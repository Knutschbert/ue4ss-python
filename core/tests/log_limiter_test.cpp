#include "test.hpp"

#include <pyue/log_limiter.hpp>

#include <atomic>
#include <thread>
#include <vector>

using Decision = pyue::LogLimiter::Decision;

int main()
{
    {
        pyue::LogLimiter limiter(3);
        PYUE_CHECK(limiter.should_log("a") == Decision::Log);
        PYUE_CHECK(limiter.should_log("a") == Decision::Log);
        PYUE_CHECK(limiter.should_log("a") == Decision::LogAndMute); // the last one says it is muting from now on
        PYUE_CHECK(limiter.should_log("a") == Decision::Mute);
        PYUE_CHECK(limiter.should_log("a") == Decision::Mute);
        PYUE_CHECK(limiter.count("a") == 5);
    }
    {
        // Keys are independent.
        pyue::LogLimiter limiter(2);
        PYUE_CHECK(limiter.should_log("a") == Decision::Log);
        PYUE_CHECK(limiter.should_log("b") == Decision::Log);
        PYUE_CHECK(limiter.should_log("a") == Decision::LogAndMute);
        PYUE_CHECK(limiter.should_log("b") == Decision::LogAndMute);
        PYUE_CHECK(limiter.should_log("c") == Decision::Log);
        PYUE_CHECK(limiter.count("missing") == 0);
        limiter.reset();
        PYUE_CHECK(limiter.should_log("a") == Decision::Log);
    }
    {
        // Exactly one thread sees LogAndMute, however many race.
        pyue::LogLimiter limiter(5);
        std::atomic<int> mute_announcements{0};
        std::atomic<int> logged{0};
        std::vector<std::thread> threads;
        for (int t = 0; t < 8; ++t)
        {
            threads.emplace_back([&] {
                for (int i = 0; i < 1000; ++i)
                {
                    const auto decision = limiter.should_log("hot");
                    if (decision == Decision::LogAndMute) ++mute_announcements;
                    if (decision != Decision::Mute) ++logged;
                }
            });
        }
        for (auto& thread : threads) thread.join();
        PYUE_CHECK(mute_announcements.load() == 1);
        PYUE_CHECK(logged.load() == 5);
        PYUE_CHECK(limiter.count("hot") == 8000);
    }
    return PYUE_TEST_MAIN_RESULT();
}
