#include "orders/OrderCore.h"

#include <utility>

long OrderCore::ID = 0;

OrderCore::OrderCore(std::string username, const int securityId)
    : orderId_(ID++), username_(std::move(username)), securityId_(securityId)
{
}

OrderCore::OrderCore(const long orderId, std::string username, const int securityId)
    : orderId_(orderId), username_(std::move(username)), securityId_(securityId)
{
}


