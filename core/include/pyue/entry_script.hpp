#pragma once

#include <filesystem>
#include <optional>
#include <system_error>

#include <pyue/runtime_config.hpp>

namespace pyue
{
    // The script to run at startup, if it exists and is a regular file.
    // A missing main.py is not an error: the mod simply has no startup script.
    inline auto resolve_entry_script(const RuntimeConfig& config) -> std::optional<std::filesystem::path>
    {
        std::error_code ec;
        if (std::filesystem::is_regular_file(config.main_script, ec) && !ec)
        {
            return config.main_script;
        }
        return std::nullopt;
    }
}
