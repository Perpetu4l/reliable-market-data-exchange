#pragma once

#include "OrderCore.h"

class Order : public OrderCore {
public:
    Order(const OrderCore& orderCore, long price, uint32_t quantity, bool isBuy);

    [[nodiscard]] long Price() const
    {
        return price_;
    }

    [[nodiscard]] uint32_t InitialQuantity() const
    {
        return initialQuantity_;
    }

    [[nodiscard]] uint32_t CurrentQuantity() const
    {
        return currentQuantity_;
    }

    [[nodiscard]] bool IsBuy() const
    {
        return isBuy_;
    }

    friend class OrderBookEntry;

    void DecreaseQuantity(uint32_t quantity);

private:
    long price_{};
    uint32_t initialQuantity_{};
    uint32_t currentQuantity_{};
    bool isBuy_{};
};

