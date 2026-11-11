#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace pyue
{
    // Splits a stream of text chunks into complete lines.
    //
    // Python's print() writes the text and the newline as separate write() calls, and tracebacks
    // arrive in arbitrary pieces, so output has to be buffered until a newline shows up before
    // it can go to the line-oriented UE4SS log. "\n" and "\r\n" both terminate a line; the
    // terminator is not part of the returned line.
    class LineBuffer
    {
    public:
        auto append(std::string_view chunk) -> std::vector<std::string>
        {
            std::vector<std::string> lines;
            for (const char c : chunk)
            {
                if (c == '\n')
                {
                    if (!m_pending.empty() && m_pending.back() == '\r') m_pending.pop_back();
                    lines.push_back(std::move(m_pending));
                    m_pending.clear();
                }
                else
                {
                    m_pending.push_back(c);
                }
            }
            return lines;
        }

        // Returns the unterminated tail (if any) and resets the buffer.
        auto flush() -> std::vector<std::string>
        {
            std::vector<std::string> lines;
            if (!m_pending.empty())
            {
                if (m_pending.back() == '\r') m_pending.pop_back();
                lines.push_back(std::move(m_pending));
                m_pending.clear();
            }
            return lines;
        }

        auto has_pending() const -> bool { return !m_pending.empty(); }

    private:
        std::string m_pending{};
    };
}
