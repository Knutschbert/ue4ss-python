#pragma once

#include <string>
#include <string_view>

// How the typings name engine members that are not Python identifiers (Blueprint variables and functions may contain spaces: "Sun height").
// The stub generator (python/lib/ue4ss/stubgen.py, _identifier) and the runtime must agree, so that the name a stub declares works on the object:
// every character that is not a letter, digit or underscore becomes '_', a leading digit gets a '_' in front, and a Python keyword gets a '_' behind.
// Characters above 127 count as letters (the stubs use the Unicode-aware \w).
namespace pyue
{
    namespace detail
    {
        template <typename Char>
        constexpr auto is_word_char(Char c) -> bool
        {
            const auto v = static_cast<unsigned long>(static_cast<std::make_unsigned_t<Char>>(c));
            return (v >= '0' && v <= '9') || (v >= 'a' && v <= 'z') || (v >= 'A' && v <= 'Z') || v == '_' || v > 127;
        }

        template <typename Char>
        auto is_python_keyword(std::basic_string_view<Char> text) -> bool
        {
            static constexpr const char* keywords[] = {"False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
                                                       "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is",
                                                       "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"};
            for (const char* keyword : keywords)
            {
                std::size_t i = 0;
                for (; keyword[i] != '\0' && i < text.size(); ++i)
                {
                    if (static_cast<unsigned long>(static_cast<std::make_unsigned_t<Char>>(text[i])) != static_cast<unsigned char>(keyword[i])) break;
                }
                if (keyword[i] == '\0' && i == text.size()) return true;
            }
            return false;
        }
    }

    template <typename Char>
    auto python_identifier(std::basic_string_view<Char> name) -> std::basic_string<Char>
    {
        std::basic_string<Char> out;
        out.reserve(name.size() + 1);
        for (const Char c : name) out.push_back(detail::is_word_char(c) ? c : static_cast<Char>('_'));
        if (out.empty() || (out[0] >= static_cast<Char>('0') && out[0] <= static_cast<Char>('9'))) out.insert(out.begin(), static_cast<Char>('_'));
        if (detail::is_python_keyword(std::basic_string_view<Char>(out))) out.push_back(static_cast<Char>('_'));
        return out;
    }

    template <typename Char>
    auto python_identifier(const std::basic_string<Char>& name) -> std::basic_string<Char>
    {
        return python_identifier(std::basic_string_view<Char>(name));
    }

    // True if `requested` is the identifier form of `engine_name` and differs from it: an alias that the runtime accepts when no member has
    // exactly that name.
    template <typename Char>
    auto is_identifier_alias(std::basic_string_view<Char> requested, std::basic_string_view<Char> engine_name) -> bool
    {
        return requested != engine_name && python_identifier(engine_name) == requested;
    }
}
