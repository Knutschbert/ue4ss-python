#pragma once

#include <filesystem>
#include <stdexcept>
#include <vector>

namespace pyue
{
    // Everything the embedded interpreter needs to know about the mod's on-disk layout.
    // Pure data so it can be built and tested without Python or Unreal headers.
    //
    //   <mod>/dlls/main.dll, python312.dll, python312.zip, *.pyd   <- home
    //   <mod>/python_scripts/main.py                               <- scripts_dir
    //
    // The folder must not be called "scripts": UE4SS treats any mod folder containing a
    // scripts directory (case-insensitive) as a Lua mod and complains about the missing main.lua.
    struct RuntimeConfig
    {
        std::filesystem::path home;
        std::filesystem::path scripts_dir;
        std::filesystem::path main_script;
        std::vector<std::filesystem::path> module_search_paths;
    };

    // stdlib_zip_name is e.g. "python312.zip" (the embeddable distribution's stdlib archive).
    inline auto make_runtime_config(const std::filesystem::path& dlls_dir, const std::filesystem::path& scripts_dir, const std::filesystem::path& stdlib_zip_name)
            -> RuntimeConfig
    {
        if (dlls_dir.empty()) throw std::invalid_argument("dlls_dir is empty");
        if (scripts_dir.empty()) throw std::invalid_argument("scripts_dir is empty");
        if (stdlib_zip_name.empty()) throw std::invalid_argument("stdlib_zip_name is empty");

        RuntimeConfig config;
        config.home = dlls_dir;
        config.scripts_dir = scripts_dir;
        config.main_script = scripts_dir / "main.py";
        // Order matters: stdlib first, then extension modules (.pyd live next to the dlls),
        // then user scripts, so a script cannot shadow a stdlib module by accident.
        config.module_search_paths = {dlls_dir / stdlib_zip_name, dlls_dir, scripts_dir};
        return config;
    }

    // <mod>/dlls -> <mod>/python_scripts
    inline auto default_scripts_dir(const std::filesystem::path& dlls_dir) -> std::filesystem::path
    {
        return dlls_dir.parent_path() / "python_scripts";
    }
}
