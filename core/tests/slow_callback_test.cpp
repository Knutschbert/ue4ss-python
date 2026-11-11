#include "test.hpp"

#include <pyue/slow_callback.hpp>

using namespace std::chrono_literals;

int main()
{
    {
        pyue::SlowCallbackPolicy policy; // 50 ms
        PYUE_CHECK(policy.threshold_ms() == 50);
        PYUE_CHECK(!policy.check("hook A", 49ms).has_value());   // fast enough
        PYUE_CHECK(!policy.check("hook A", 0ns).has_value());
        const auto slow = policy.check("hook A", 60ms);
        PYUE_CHECK(slow.has_value());
        PYUE_CHECK(slow->find("hook A") != std::string::npos);
        PYUE_CHECK(slow->find("60 ms") != std::string::npos);
        PYUE_CHECK(slow->find("limit 50 ms") != std::string::npos);
        PYUE_CHECK(policy.check("hook A", 50ms).has_value());    // the threshold itself counts as slow
    }
    {
        // a label is reported a few times, the last message says it is muted, then it is quiet; labels are independent
        pyue::SlowCallbackPolicy policy(10, 3);
        const auto first = policy.check("task", 20ms);
        const auto second = policy.check("task", 20ms);
        const auto third = policy.check("task", 20ms);
        PYUE_CHECK(first && second && third);
        PYUE_CHECK(first->find("not logged") == std::string::npos);
        PYUE_CHECK(third->find("further reports for it are not logged") != std::string::npos);
        PYUE_CHECK(!policy.check("task", 20ms).has_value());
        PYUE_CHECK(!policy.check("task", 900ms).has_value());
        PYUE_CHECK(policy.check("another task", 20ms).has_value());
        policy.reset();
        PYUE_CHECK(policy.check("task", 20ms).has_value());
    }
    {
        pyue::SlowCallbackPolicy policy(10);
        policy.set_threshold_ms(0);                              // off
        PYUE_CHECK(!policy.check("x", 10s).has_value());
        policy.set_threshold_ms(-5);                             // negative means off as well
        PYUE_CHECK(policy.threshold_ms() == 0);
        policy.set_threshold_ms(100);
        PYUE_CHECK(!policy.check("x", 99ms).has_value());
        PYUE_CHECK(policy.check("x", 101ms).has_value());
    }
    return PYUE_TEST_MAIN_RESULT();
}
