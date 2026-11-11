#pragma once

#include <cstddef>
#include <cstdint>

namespace pyue
{
    // Identity of an Unreal FName: the comparison index plus the numeric suffix ("Foo_3" is
    // index("Foo") with number 4). Two names are the same name exactly when both fields match, which is
    // also how the engine compares them (case-insensitively, because the comparison index is).
    struct NameId
    {
        std::uint32_t index{0};
        std::int32_t number{0};

        friend auto operator==(const NameId&, const NameId&) -> bool = default;

        // FName() / NAME_None
        auto is_none() const -> bool { return index == 0 && number == 0; }
    };

    inline auto hash_name(const NameId& id) -> std::size_t
    {
        const auto packed = (static_cast<std::uint64_t>(id.index) << 32) | static_cast<std::uint32_t>(id.number);
        // splitmix64 finaliser: spreads consecutive indices across the hash space
        auto x = packed + 0x9e3779b97f4a7c15ULL;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
        return static_cast<std::size_t>(x ^ (x >> 31));
    }
}
