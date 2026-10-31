project(tests)

include(FetchContent)
FetchContent_Declare(
        googletest
        URL https://github.com/google/googletest/archive/03597a01ee50ed33e9dfd640b249b4be3799d395.zip
)
FetchContent_MakeAvailable(googletest)
enable_testing()

add_executable(Tests OrderBookTests.cpp MatchingEngineTests.cpp OrderBookEntryTests.cpp ReliabilitySkeletonTests.cpp OrderEntryNetworkingTests.cpp)
target_link_libraries(Tests OrderBook MDFeed OrderEntry GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(Tests)

# This invokes real local processes and skips only when the host explicitly
# disallows socket binding (for example, a restricted CI sandbox).
find_package(Python3 COMPONENTS Interpreter QUIET)
if(Python3_Interpreter_FOUND)
    add_test(NAME LiveReliabilityBaseline
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/live_reliability_e2e.py
            --build-dir ${CMAKE_BINARY_DIR})
    set_tests_properties(LiveReliabilityBaseline PROPERTIES
        SKIP_RETURN_CODE 77
        TIMEOUT 120)

    add_test(NAME LiveReliabilitySchedulerSlow
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/live_reliability_e2e.py
            --build-dir ${CMAKE_BINARY_DIR} --slow)
    set_tests_properties(LiveReliabilitySchedulerSlow PROPERTIES
        LABELS "slow"
        SKIP_RETURN_CODE 77
        TIMEOUT 240)

    add_test(NAME GuiSmokeTest
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/gui_smoke_test.py
            --build-dir ${CMAKE_BINARY_DIR})
    set_tests_properties(GuiSmokeTest PROPERTIES
        SKIP_RETURN_CODE 77
        TIMEOUT 90)
endif()
