#pragma once

#include <optional>
#include <string>
#include <string_view>

#include <pyue/name_id.hpp>

// Everything that needs Unreal headers lives behind this interface, so that Python.h and the
// Unreal headers are never included in the same translation unit. Strings cross it as UTF-16
// (std::wstring on Windows), which is what both Unreal (TCHAR) and Python (wchar_t) use.
namespace pyue::bridge
{
    // FName -> text. Throws UEError if the name cannot be resolved.
    auto name_to_string(NameId id) -> std::wstring;

    // text -> FName. With add == true an unknown name is added to the engine's name table (like
    // FName("x") in UE4SS Lua); with add == false it is only looked up and nullopt means "not found".
    auto name_from_string(std::wstring_view text, bool add) -> std::optional<NameId>;

    // Could an object with this path exist? Every name in it (the package, then segments split at '.' and ':') must already be in
    // the engine's name table. Lookups call this first so that probing for a missing object never adds names.
    auto path_names_exist(std::wstring_view path) -> bool;

    // Builds an FString from `text` and reads it back (self test of the FString conversion).
    auto fstring_round_trip(std::wstring_view text) -> std::wstring;
}
