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
    # Locate the Python 3 interpreter that actually has pybind11 installed
    # (preferring the active PATH python3 or macOS Framework Python over bare Homebrew python3.15).
    find_program(_PATH_PYTHON3 python3)
    set(_CANDIDATE_PYTHONS
        "${Python3_EXECUTABLE}"
        "${_PATH_PYTHON3}"
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
        "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
        "/opt/homebrew/bin/python3"
        "/usr/local/bin/python3"
        "/usr/bin/python3"
    )
    foreach (_PY_CANDIDATE IN LISTS _CANDIDATE_PYTHONS)
        if (_PY_CANDIDATE AND EXISTS "${_PY_CANDIDATE}")
            execute_process(
                COMMAND "${_PY_CANDIDATE}" -c "import pybind11; print(pybind11.get_cmake_dir())"
                OUTPUT_VARIABLE _PYBIND11_CMAKE_DIR
                OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_QUIET
                RESULT_VARIABLE _PYBIND11_RES
            )
            if (_PYBIND11_RES EQUAL 0 AND EXISTS "${_PYBIND11_CMAKE_DIR}")
                set(Python3_EXECUTABLE "${_PY_CANDIDATE}" CACHE FILEPATH "Python3 executable with pybind11" FORCE)
                set(PYTHON_EXECUTABLE "${_PY_CANDIDATE}" CACHE FILEPATH "Python executable for pybind11" FORCE)
                set(pybind11_DIR "${_PYBIND11_CMAKE_DIR}" CACHE PATH "pybind11 CMake directory" FORCE)
                list(APPEND CMAKE_PREFIX_PATH "${_PYBIND11_CMAKE_DIR}")
                break ()
            endif ()
        endif ()
    endforeach ()

    find_package(pybind11 CONFIG QUIET)
    if (NOT pybind11_FOUND)
        find_package(pybind11 QUIET)
    endif ()

    if (pybind11_FOUND)
        message(STATUS "Building Python bindings with Python=${Python3_EXECUTABLE} and pybind11=${pybind11_DIR}")
        add_subdirectory(lib/OrderBook/python)
        add_subdirectory(lib/MDFeed/python)
        add_subdirectory(lib/OrderEntry/python)
    else ()
        message(WARNING "pybind11 not found, skipping C++ Python bindings (orderbook/__init__.py will use pure-Python fallback)")
    endif ()
endif ()
