#include "test.hpp"

#include <pyue/version.hpp>

int main()
{
    PYUE_CHECK(pyue::version_major == 0);
    PYUE_CHECK(pyue::version_minor >= 1);
    return PYUE_TEST_MAIN_RESULT();
}
