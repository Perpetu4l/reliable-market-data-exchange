set(HEADER_FILES
        include/messages/Messages.h
        include/utils/RingBuffer.h
        include/utils/PublisherConfig.h
        include/publisher/MulticastPublisher.h
        include/publisher/MarketDataPublisher.h
        include/publisher/MDAdapter.h
        include/publisher/MulticastPublisherThread.h
        include/receiver/ReceiverConfig.h
        include/receiver/MulticastReceiver.h
        include/reliability/ReliabilityTypes.h
        include/reliability/SequenceTracker.h
        include/reliability/DualFeed.h
        include/reliability/RecoveryHistory.h
        include/reliability/RecoveryProtocol.h
        include/reliability/Snapshot.h
        include/reliability/RecoveryScheduler.h
        include/reliability/FaultInjector.h
        include/reliability/Experiments.h
        include/reliability/Metrics.h
)

set(SOURCE_FILES
        src/publisher/MulticastPublisher.cpp
        src/publisher/MarketDataPublisher.cpp
        src/publisher/MulticastPublisherThread.cpp
        src/receiver/MulticastReceiver.cpp
        src/reliability/SequenceTracker.cpp
        src/reliability/DualFeed.cpp
        src/reliability/RecoveryHistory.cpp
        src/reliability/RecoveryProtocol.cpp
        src/reliability/Snapshot.cpp
        src/reliability/RecoveryScheduler.cpp
        src/reliability/FaultInjector.cpp
        src/reliability/Experiments.cpp
        src/reliability/Metrics.cpp
)

add_library(MDFeed STATIC ${SOURCE_FILES} ${HEADER_FILES})

target_include_directories(MDFeed PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

find_package(Threads REQUIRED)
target_link_libraries(MDFeed PUBLIC Threads::Threads Boost::headers)
