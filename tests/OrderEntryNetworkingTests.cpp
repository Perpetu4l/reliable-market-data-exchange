#include "messages/OrderMessages.h"
#include "utils/OrderBuffer.h"

#include <gtest/gtest.h>

TEST(OrderEntryProtocolTests, ValidatesExactPackedFrameSizes)
{
    using namespace orderentry;
    EXPECT_TRUE(message_utils::is_valid_message_length(MessageType::NEW_ORDER, sizeof(NewOrderMessage)));
    EXPECT_TRUE(message_utils::is_valid_message_length(MessageType::CLIENT_HELLO, sizeof(ClientHelloMessage)));
    EXPECT_TRUE(message_utils::is_valid_message_length(MessageType::SERVER_WELCOME, sizeof(ServerWelcomeMessage)));
    EXPECT_FALSE(message_utils::is_valid_message_length(MessageType::NEW_ORDER, sizeof(NewOrderMessage) - 1));
    EXPECT_FALSE(message_utils::is_valid_message_length(static_cast<MessageType>(999), 64));
}

TEST(OrderEntryQueueTests, PreservesBoundedFifoSemantics)
{
    orderentry::OrderRingBuffer queue(2);
    orderentry::OrderBuffer first;
    first.client_fd = 10;
    orderentry::OrderBuffer second;
    second.client_fd = 11;
    orderentry::OrderBuffer third;
    third.client_fd = 12;

    EXPECT_TRUE(queue.push(first));
    EXPECT_TRUE(queue.push(second));
    EXPECT_FALSE(queue.push(third));

    orderentry::OrderBuffer received;
    ASSERT_TRUE(queue.pop(received));
    EXPECT_EQ(received.client_fd, 10);
    ASSERT_TRUE(queue.pop(received));
    EXPECT_EQ(received.client_fd, 11);
    EXPECT_FALSE(queue.pop(received));
}
