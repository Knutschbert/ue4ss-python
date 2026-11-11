#pragma once

// Minimal test harness so core/ tests have no external dependencies.
#include <cstdio>

namespace pyue::test
{
    inline int& failures()
    {
        static int count = 0;
        return count;
    }
}

#define PYUE_CHECK(expr)                                                              \
    do                                                                                \
    {                                                                                 \
        if (!(expr))                                                                  \
        {                                                                             \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #expr); \
            ++::pyue::test::failures();                                               \
        }                                                                             \
    } while (false)

#define PYUE_TEST_MAIN_RESULT() (::pyue::test::failures() == 0 ? 0 : 1)
