add_library(pyue_core INTERFACE)
target_include_directories(pyue_core INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_compile_features(pyue_core INTERFACE cxx_std_20)
