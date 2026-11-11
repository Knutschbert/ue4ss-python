set(TARGET PythonScripting)

# Pinned CPython headers and import library, fetched by tools/setup_windows.ps1.
set(PYUE_PYTHON_SDK "${CMAKE_SOURCE_DIR}/third_party/python/sdk" CACHE PATH "CPython include/ and libs/ directory")
if(NOT EXISTS "${PYUE_PYTHON_SDK}/include/Python.h")
    message(FATAL_ERROR "Python.h not found in ${PYUE_PYTHON_SDK}/include. Run tools/setup_windows.ps1.")
endif()

add_library(${TARGET} SHARED dllmain.cpp CallbackRunner.cpp GameThreadExecutor.cpp HookApi.cpp ObjectApi.cpp NewObjectApi.cpp ObjectRegistry.cpp PropertyApi.cpp PyArray.cpp PyCall.cpp PyFixedArray.cpp PyFName.cpp PyHooks.cpp PyKeys.cpp PyKinds.cpp PyMapSet.cpp PyNewObjects.cpp PyProperties.cpp PyRotation.cpp PyStruct.cpp PyUObject.cpp PythonRuntime.cpp Reload.cpp SafeCall.cpp SlowCallback.cpp ThreadProbe.cpp UeModule.cpp UnrealBridge.cpp DelegateApi.cpp)
target_include_directories(${TARGET} PRIVATE "${PYUE_PYTHON_SDK}/include")
# PolyHook_2 (what UE4SS itself uses for its detours) for the manual-detour self test (_selftest_manual_pe_detour).
target_include_directories(${TARGET} PRIVATE "${CMAKE_SOURCE_DIR}/build/_deps/polyhook2-src")
target_link_libraries(${TARGET} PUBLIC UE4SS pyue_core "${PYUE_PYTHON_SDK}/libs/python312.lib" "${CMAKE_BINARY_DIR}/${CMAKE_BUILD_TYPE}/lib/PolyHook_2.lib")
set_target_properties(${TARGET} PROPERTIES OUTPUT_NAME "main")
