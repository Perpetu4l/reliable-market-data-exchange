cmake_minimum_required(VERSION 3.24)

message(STATUS "Using system default compiler")

project(LimitOrderBook)

# Test discovery must be enabled at the top level for CTest to see tests from tests/.
enable_testing()

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if (CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(-Wall -Wextra -Wpedantic)
endif ()

# Dependencies are discovered through normal CMake package search paths. On a
# Homebrew installation CMake normally finds these automatically; callers may
# still provide Boost_ROOT or spdlog_DIR when using a non-standard prefix.
find_package(Boost 1.82 REQUIRED)
find_package(spdlog REQUIRED)

add_subdirectory(lib)
add_subdirectory(src)

option(BUILD_PROJECT_TESTS "Build the project's GoogleTest test suite" ON)
if (BUILD_PROJECT_TESTS)
    add_subdirectory(tests)
endif ()

option(BUILD_BENCHMARKS "Build Google Benchmark targets" ON)
if (BUILD_BENCHMARKS)
    add_subdirectory(benchmarks)
endif ()

option(BUILD_PYTHON_BINDINGS "Build Python bindings" ON)
if (BUILD_PYTHON_BINDINGS)
    find_package(pybind11 QUIET)
    if (pybind11_FOUND)
        message(STATUS "Building Python bindings")
        add_subdirectory(lib/OrderBook/python)
        add_subdirectory(lib/MDFeed/python)
        add_subdirectory(lib/OrderEntry/python)
    else ()
        message(WARNING "pybind11 not found, skipping Python bindings")
    endif ()
endif ()
