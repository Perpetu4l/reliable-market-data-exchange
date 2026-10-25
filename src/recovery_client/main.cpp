#include "reliability/RecoveryTcp.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {
using namespace mdfeed::reliability;

const char* status_name(const RecoveryStatus status)
{
    switch (status) {
    case RecoveryStatus::OK: return "OK";
    case RecoveryStatus::TOO_OLD: return "TOO_OLD";
    case RecoveryStatus::BAD_REQUEST: return "BAD_REQUEST";
    case RecoveryStatus::ERROR: return "ERROR";
    }
    return "UNKNOWN";
}

void usage(const char* program)
{
    std::cout << "Usage: " << program
              << " --server <IPv4> --start <sequence> --end <sequence>"
                 " [--port <recovery-port>] [--subscriber <id>]\n";
}
} // namespace

int main(const int argc, char* argv[])
{
    RecoveryTcpClient::Endpoint endpoint;
    std::string subscriber_id{"recovery-cli"};
    MissingRange range;
    bool have_server = false;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") {
            usage(argv[0]);
            return EXIT_SUCCESS;
        }
        if (index + 1 >= argc) {
            usage(argv[0]);
            return EXIT_FAILURE;
        }
        const std::string value = argv[++index];
        if (argument == "--server") { endpoint.host = value; have_server = true; }
        else if (argument == "--port") endpoint.port = static_cast<uint16_t>(std::stoul(value));
        else if (argument == "--subscriber") subscriber_id = value;
        else if (argument == "--start") range.start_sequence = std::stoull(value);
        else if (argument == "--end") range.end_sequence = std::stoull(value);
        else {
            usage(argv[0]);
            return EXIT_FAILURE;
        }
    }
    if (!have_server || !range.valid()) {
        usage(argv[0]);
        return EXIT_FAILURE;
    }

    RecoveryTcpClient client(endpoint);
    std::string error;
    const auto response = client.request_missing_data({subscriber_id, range}, error);
    if (!response) {
        std::cerr << "Recovery request failed: " << error << '\n';
        return EXIT_FAILURE;
    }
    std::cout << "Recovery status: " << status_name(response->status) << '\n';
    if (!response->detail.empty()) std::cout << "Detail: " << response->detail << '\n';
    std::cout << "Recovered events: " << response->events.size() << '\n';
    for (const auto& event : response->events) {
        std::cout << "  seq=" << event.header.sequence_number
                  << " type=" << event.header.message_type
                  << " instrument=" << event.header.instrument_id << '\n';
    }
    return response->status == RecoveryStatus::OK ? EXIT_SUCCESS : EXIT_FAILURE;
}
