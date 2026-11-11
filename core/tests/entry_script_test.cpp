#include "test.hpp"

#include <pyue/entry_script.hpp>

#include <fstream>

namespace fs = std::filesystem;

int main()
{
    const fs::path root = fs::temp_directory_path() / "pyue_entry_script_test";
    fs::remove_all(root);
    const fs::path dlls = root / "dlls";
    const fs::path scripts = root / "python_scripts";
    fs::create_directories(dlls);
    fs::create_directories(scripts);

    const auto config = pyue::make_runtime_config(dlls, scripts, "python312.zip");

    // No main.py yet.
    PYUE_CHECK(!pyue::resolve_entry_script(config).has_value());

    // A directory called main.py is not a script.
    fs::create_directory(scripts / "main.py");
    PYUE_CHECK(!pyue::resolve_entry_script(config).has_value());
    fs::remove(scripts / "main.py");

    // A regular file is found.
    std::ofstream(scripts / "main.py") << "print('hi')\n";
    const auto entry = pyue::resolve_entry_script(config);
    PYUE_CHECK(entry.has_value());
    PYUE_CHECK(entry && *entry == scripts / "main.py");

    // Missing scripts directory entirely.
    const auto config2 = pyue::make_runtime_config(dlls, root / "nope", "python312.zip");
    PYUE_CHECK(!pyue::resolve_entry_script(config2).has_value());

    fs::remove_all(root);
    return PYUE_TEST_MAIN_RESULT();
}
