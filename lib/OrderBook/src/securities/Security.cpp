#include "securities/Security.h"

#include <utility>

Security::Security(std::string name, std::string ticker, const int securityId)
    : name_(std::move(name)), ticker_(std::move(ticker)), securityId_(securityId)
{
}
