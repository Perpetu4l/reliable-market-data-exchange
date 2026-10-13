#pragma once

#include "string"

class Security {
public:
    Security(std::string name, std::string ticker, int securityId);

    [[nodiscard]] int GetSecurityId() const
    {
        return securityId_;
    }

private:
    const std::string name_;
    const std::string ticker_;
    const int securityId_;
};


