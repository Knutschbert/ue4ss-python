#include "test.hpp"

#include <pyue/runtime_config.hpp>

#include <stdexcept>

using std::filesystem::path;

int main()
{
    const path dlls = path("Mods") / "PythonScripting" / "dlls";
    const auto scripts = pyue::default_scripts_dir(dlls);
    PYUE_CHECK(scripts == path("Mods") / "PythonScripting" / "python_scripts");
    PYUE_CHECK(scripts.filename() != "scripts"); // would be detected as a Lua mod by UE4SS

    const auto config = pyue::make_runtime_config(dlls, scripts, "python312.zip");
    PYUE_CHECK(config.home == dlls);
    PYUE_CHECK(config.scripts_dir == scripts);
    PYUE_CHECK(config.main_script == scripts / "main.py");
    PYUE_CHECK(config.module_search_paths.size() == 3);
    PYUE_CHECK(config.module_search_paths[0] == dlls / "python312.zip");
    PYUE_CHECK(config.module_search_paths[1] == dlls);
    PYUE_CHECK(config.module_search_paths[2] == scripts);

    bool threw = false;
    try { (void)pyue::make_runtime_config({}, scripts, "python312.zip"); } catch (const std::invalid_argument&) { threw = true; }
    PYUE_CHECK(threw);

    threw = false;
    try { (void)pyue::make_runtime_config(dlls, {}, "python312.zip"); } catch (const std::invalid_argument&) { threw = true; }
    PYUE_CHECK(threw);

    threw = false;
    try { (void)pyue::make_runtime_config(dlls, scripts, {}); } catch (const std::invalid_argument&) { threw = true; }
    PYUE_CHECK(threw);

    return PYUE_TEST_MAIN_RESULT();
}
