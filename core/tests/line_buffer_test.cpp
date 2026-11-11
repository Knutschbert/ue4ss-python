#include "test.hpp"

#include <pyue/line_buffer.hpp>

using Lines = std::vector<std::string>;

int main()
{
    {
        // print("hi") arrives as "hi" then "\n".
        pyue::LineBuffer buffer;
        PYUE_CHECK(buffer.append("hi").empty());
        PYUE_CHECK(buffer.has_pending());
        PYUE_CHECK((buffer.append("\n") == Lines{"hi"}));
        PYUE_CHECK(!buffer.has_pending());
    }
    {
        pyue::LineBuffer buffer;
        PYUE_CHECK((buffer.append("a\nb\nc") == Lines{"a", "b"}));
        PYUE_CHECK((buffer.append("d\n") == Lines{"cd"}));
    }
    {
        // CRLF is normalised, also when split across chunks.
        pyue::LineBuffer buffer;
        PYUE_CHECK((buffer.append("x\r\ny\r") == Lines{"x"}));
        PYUE_CHECK((buffer.append("\n") == Lines{"y"}));
    }
    {
        // Empty lines are preserved.
        pyue::LineBuffer buffer;
        PYUE_CHECK((buffer.append("\n\nz\n") == Lines{"", "", "z"}));
    }
    {
        pyue::LineBuffer buffer;
        PYUE_CHECK(buffer.append("").empty());
        PYUE_CHECK(buffer.flush().empty());
    }
    {
        // flush() returns the unterminated tail once.
        pyue::LineBuffer buffer;
        buffer.append("tail");
        PYUE_CHECK((buffer.flush() == Lines{"tail"}));
        PYUE_CHECK(buffer.flush().empty());
        PYUE_CHECK(!buffer.has_pending());
    }
    {
        // UTF-8 bytes pass through untouched (including bytes >= 0x80).
        pyue::LineBuffer buffer;
        PYUE_CHECK((buffer.append("caf\xC3\xA9\n") == Lines{"caf\xC3\xA9"}));
    }
    return PYUE_TEST_MAIN_RESULT();
}
