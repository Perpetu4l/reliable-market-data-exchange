add_executable(exchange
        exchange/Exchange.cpp
        exchange/SymbolManager.cpp
        exchange/main.cpp
)

target_include_directories(exchange PRIVATE exchange)
target_link_libraries(exchange OrderBook MDFeed OrderEntry)

add_executable(client client/main.cpp)
target_link_libraries(client MDFeed)

add_executable(order_server order_server/main.cpp)
target_link_libraries(order_server OrderEntry)

add_executable(order_client order_client/main.cpp)
target_link_libraries(order_client OrderEntry)

# Independent automatic order generator.  This is intentionally a separate
# process from the exchange so local benchmarks still use real TCP sockets.
add_executable(workload_client workload_client/main.cpp)
target_link_libraries(workload_client OrderEntry)

add_executable(reliability_demo reliability_demo/main.cpp)
target_link_libraries(reliability_demo MDFeed)

# A narrow command-line verifier for the live recovery TCP endpoint. It does
# not fabricate market data: every displayed event came from the exchange's
# retained publisher history.
add_executable(recovery_client recovery_client/main.cpp)
target_link_libraries(recovery_client MDFeed)

add_executable(reliable_subscriber reliable_subscriber/main.cpp)
target_link_libraries(reliable_subscriber MDFeed)
