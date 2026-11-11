#include "UnrealBridge.hpp"

#include <algorithm>
#include <cwctype>

#include <Unreal/Core/Containers/FString.hpp>
#include <Unreal/FText.hpp>
#include <Unreal/NameTypes.hpp>

#include <pyue/errors.hpp>

#ifdef WITH_CASE_PRESERVING_NAME
#error "FName with a display index (CasePreserving builds) is not supported yet; build the Game target"
#endif

namespace pyue::bridge
{
    namespace
    {
        using namespace RC;

        static_assert(sizeof(Unreal::FName) == 8, "FName layout differs from the (index, number) pair assumed by NameId");
        static_assert(sizeof(TCHAR) == sizeof(wchar_t), "TCHAR must be wchar_t so strings can cross the bridge unchanged");

        auto to_unreal_name(NameId id) -> Unreal::FName
        {
            return Unreal::FName(static_cast<uint32_t>(id.index), static_cast<uint32_t>(id.number));
        }

        auto from_unreal_name(const Unreal::FName& name) -> NameId
        {
            return NameId{static_cast<std::uint32_t>(name.GetComparisonIndex().ToUnstableInt()), static_cast<std::int32_t>(name.GetNumber())};
        }

        auto equals_none_ignoring_case(std::wstring_view text) -> bool
        {
            constexpr std::wstring_view none = L"none";
            return text.size() == none.size() && std::equal(text.begin(), text.end(), none.begin(), [](wchar_t a, wchar_t b) {
                       return std::towlower(a) == b;
                   });
        }
    }

    auto name_to_string(NameId id) -> std::wstring
    {
        return std::wstring(to_unreal_name(id).ToString());
    }

    auto name_from_string(std::wstring_view text, bool add) -> std::optional<NameId>
    {
        // FName(StringViewType) passes view.data() to the engine and ignores the length, so a slice of a longer string
        // would be looked up with the rest of that string glued on. Always give the engine a NUL-terminated copy.
        const StringType terminated(text);
        const Unreal::FName name(terminated.c_str(), add ? Unreal::FNAME_Add : Unreal::FNAME_Find);
        const NameId id = from_unreal_name(name);
        // A failed lookup yields NAME_None, which is also the right answer for the text "None".
        if (!add && id.is_none() && !equals_none_ignoring_case(text)) return std::nullopt;
        return id;
    }

    auto path_names_exist(std::wstring_view path) -> bool
    {
        size_t start = 0;
        while (start <= path.size())
        {
            size_t end = path.find_first_of(L".:", start); // a package name such as /Script/Engine is ONE name, slashes included
            if (end == std::wstring_view::npos) end = path.size();
            const std::wstring_view segment = path.substr(start, end - start);
            if (!segment.empty() && !name_from_string(segment, false)) return false;
            start = end + 1;
        }
        return true;
    }

    auto fstring_round_trip(std::wstring_view text) -> std::wstring
    {
        const Unreal::FString string{StringType(text)};
        return std::wstring(*string, static_cast<size_t>(string.Len()));
    }
}
