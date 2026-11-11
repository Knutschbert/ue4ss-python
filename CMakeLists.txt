cmake_minimum_required(VERSION 3.22)
project(PythonScripting LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# UE-free logic (handle table, work queue, marshalling layout, ...). Header-only
# and free of Unreal/Windows includes so it can be unit tested on any host.
add_subdirectory(core)

option(PYUE_HOST_TESTS "Build host-side unit tests for core/" OFF)
if(PYUE_HOST_TESTS)
    enable_testing()
    add_subdirectory(core/tests)
endif()

# The UE4SS mod DLL (Windows/MSVC only). Configure from this directory with
#   cmake -B build -G "Visual Studio 17 2022" -A x64 -DUE4SS_ROOT=<path to RE-UE4SS>
# and build with the same <Target>__<Config>__Win64 as the installed UE4SS (see docs/BUILDING.md).
set(UE4SS_ROOT "" CACHE PATH "Path to the RE-UE4SS checkout (Windows mod build)")
if(WIN32 AND UE4SS_ROOT)
    add_subdirectory("${UE4SS_ROOT}" "${CMAKE_BINARY_DIR}/ue4ss")
    add_subdirectory(src)
endif()
