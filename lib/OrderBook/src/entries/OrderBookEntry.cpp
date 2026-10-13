
#include "entries/OrderBookEntry.h"

#include <utility>

OrderBookEntry::OrderBookEntry(const std::shared_ptr<Limit>& parentLimit, Order currentOrder)
    : currentOrder_(std::move(currentOrder)), limit_(parentLimit)
{
}

Limit::Limit(const long price)
    : price_(price)
      , size_(0)
      , orderQuantity_(0)
      , head_(nullptr)
      , tail_(nullptr)
{
}

void Limit::AddOrder(const std::shared_ptr<OrderBookEntry>& orderBookEntry)
{
    if (head_ == nullptr) {
        // no orders on this level
        head_ = orderBookEntry;
        tail_ = orderBookEntry;
    }
    else {
        // we have orders on this level
        std::shared_ptr<OrderBookEntry> tailEntry = tail_;
        tailEntry->next = orderBookEntry;
        orderBookEntry->previous = tailEntry;
        tail_ = orderBookEntry;
    }
    size_++;
    orderQuantity_ += orderBookEntry->CurrentOrder().CurrentQuantity();
}

std::expected<void, std::string> Limit::RemoveOrder(const long orderId, const uint32_t quantity)
{
    if (!head_) [[unlikely]] {
        return std::unexpected("Order not found - limit is empty");
    }
    auto current = head_;
    while (current && current->CurrentOrder().OrderId() != orderId) {
        current = current->next;
    }
    if (!current) [[unlikely]] {
        return std::unexpected("Order not found");
    }
    if (current == head_) {
        head_ = head_->next;
        if (head_) {
            head_->previous.reset();
        }
    }
    if (current == tail_) {
        tail_ = tail_->previous.lock();
        if (tail_) {
            tail_->next = nullptr;
        }
    }
    if (const auto prev = current->previous.lock()) {
        prev->next = current->next;
    }
    if (current->next) {
        current->next->previous = current->previous;
    }
    size_--;
    orderQuantity_ -= quantity;
    return {};
}

std::list<OrderStruct> Limit::GetOrderRecords() const
{
    std::list<OrderStruct> orderRecords;
    auto entryPtr = head_;
    uint32_t queuePosition = 0;
    while (entryPtr) {
        if (Order currentOrder = entryPtr->CurrentOrder(); currentOrder.CurrentQuantity() != 0) {
            orderRecords.push_back(OrderStruct{
                currentOrder.OrderId(),
                currentOrder.CurrentQuantity(),
                currentOrder.Price(),
                currentOrder.IsBuy(),
                currentOrder.Username(),
                currentOrder.SecurityID(),
                queuePosition,
            });
            queuePosition++;
            entryPtr = entryPtr->next;
        }
    }
    return orderRecords;
}
