# Patches for other projects

## dwmapi-load-dll.patch (RE-UE4SS, `UE4SS/proxy_generator/main.cpp`)
Adds two optional game arguments to the generated UE4SS proxy (`dwmapi.dll`):

    --load-dll <full path>     load that DLL (repeatable) once the game process is up
    --load-dll-delay <ms>      wait this long first (default 0)

Nothing changes without `--load-dll`. A DLL whose file name is already loaded (the Unchained launcher injected it) is skipped. The load runs on its own thread because `DllMain`
holds the loader lock; a failure goes to `OutputDebugString`. Purpose: unattended Chivalry 2 test runs can attach `Plugins\UnchainedPlugin.dll` (ticket fx7.24).

Apply and build (x64 Native Tools PowerShell, in the RE-UE4SS checkout that `tools\setup_windows.ps1` made, same config as the UE4SS you use):

    git apply <path>\dwmapi-load-dll.patch
    cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Game__Shipping__Win64     # once
    cmake --build build --target proxy

The new `dwmapi.dll` replaces the old one in `TBL\Binaries\Win64`. Checked: the patch applies to RE-UE4SS 97adba0, and the generated code compiles with MSVC (cl /W4 /std:c++20, `/c`).
Not checked: a full build of the proxy and loading the plugin in the game.
