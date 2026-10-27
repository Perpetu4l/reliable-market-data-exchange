#include "Exchange.h"
#include <utility>
#include <sys/socket.h>
#include <cstring>
#include <algorithm>
#include <sstream>

Exchange::Exchange(const Mode mode, mdfeed::PublisherConfig md_config, const uint16_t oe_port)
    : mode_(mode), running_(false), md_config_(std::move(md_config)),
      order_entry_port_(oe_port), generator_(rd_()), price_dist_(50000, 500),
      quantity_dist_(100, 20), bool_dist_(0.5)
{
    md_publisher_ = std::make_unique<mdfeed::MarketDataPublisher>(md_config_);
    recovery_history_ = std::make_shared<mdfeed::reliability::RecoveryHistory>(
        md_config_.recovery_history_events);
    snapshot_manager_ = std::make_shared<mdfeed::reliability::SnapshotManager>(
        mdfeed::reliability::SnapshotConfig{md_config_.snapshot_interval_events,
            md_config_.snapshot_retention_count, md_config_.snapshot_interval_events != 0});
    market_data_book_builder_ = std::make_shared<mdfeed::reliability::BookBuilder>();
    recovery_service_ = std::make_shared<mdfeed::reliability::RecoveryServer>(
        recovery_history_, snapshot_manager_);
    if (md_config_.recovery_port != 0) {
        recovery_tcp_server_ = std::make_unique<mdfeed::reliability::RecoveryTcpServer>(
            mdfeed::reliability::RecoveryTcpServer::Config{md_config_.recovery_port,
                md_config_.recovery_scheduling_policy == "round-robin"
                    ? mdfeed::reliability::SchedulingPolicy::ROUND_ROBIN
                    : mdfeed::reliability::SchedulingPolicy::FCFS}, recovery_service_);
    }
    multicast_thread_ = std::make_unique<
        mdfeed::MulticastPublisherThread>(md_publisher_->get_ring_buffer(), md_config_,
            [history = recovery_history_, snapshots = snapshot_manager_, books = market_data_book_builder_]
            (const mdfeed::MessageBuffer& buffer) {
                if (buffer.length < sizeof(mdfeed::MessageHeader)) return;
                mdfeed::reliability::MarketDataEvent event;
                std::memcpy(&event.header, buffer.data, sizeof(event.header));
                event.payload.assign(reinterpret_cast<const uint8_t*>(buffer.data),
                    reinterpret_cast<const uint8_t*>(buffer.data) + buffer.length);
                history->append(event);
                if (books->apply(event) && snapshots->should_checkpoint(event.header.sequence_number)) {
                    snapshots->store_snapshot(snapshots->create_snapshot(event.header.sequence_number,
                        0, books->all_levels(), event.header.timestamp_ns));
                }
            });

    symbol_manager_ = std::make_unique<SymbolManager>(*md_publisher_);

    if (mode_ == Mode::CLIENT_ORDERS) {
        order_server_ = std::make_unique<orderentry::OrderEntryServer>(order_entry_port_);
    }
}

bool Exchange::start()
{
    if (running_.load()) return true;
    std::cout << "Starting Multicast Publisher Thread..." << std::endl;
    if (!multicast_thread_->start()) {
        std::cerr << "Failed to start multicast publisher thread" << std::endl;
        return false;
    }
    if (recovery_tcp_server_) {
        std::string recovery_error;
        if (!recovery_tcp_server_->start(recovery_error)) {
            std::cerr << "Failed to start recovery TCP server: " << recovery_error << std::endl;
            multicast_thread_->stop();
            return false;
        }
        std::cout << "Recovery TCP server listening on 0.0.0.0:"
                  << recovery_tcp_server_->port() << std::endl;
    }
    if (mode_ == Mode::CLIENT_ORDERS) {
        std::cout << "Starting Order Entry Server on port " << order_entry_port_ << "..." << std::endl;
        if (!order_server_->start()) {
            std::cerr << "Failed to start order entry server" << std::endl;
            if (recovery_tcp_server_) recovery_tcp_server_->stop();
            multicast_thread_->stop();
            return false;
        }
    }

    running_.store(true);

    if (mode_ == Mode::SIMULATION) {
        std::cout << "Exchange started in SIMULATION mode" << std::endl;
        initialize_simulation_books();
    }
    else {
        std::cout << "Exchange started in CLIENT_ORDERS mode" << std::endl;
        std::cout << "Order books initialized empty, waiting for client orders..." << std::endl;
    }
    print_supported_symbols();
    return true;
}

void Exchange::stop()
{
    if (!running_.load()) return;
    std::cout << "Stopping Enhanced Exchange..." << std::endl;
    running_.store(false);
    if (order_server_) { order_server_->stop(); }
    if (recovery_tcp_server_) { recovery_tcp_server_->stop(); }
    if (multicast_thread_) { multicast_thread_->stop(); }
    print_final_stats();
}

void Exchange::run()
{
    if (!start()) {
        std::cerr << "Failed to start exchange" << std::endl;
        return;
    }
    if (mode_ == Mode::SIMULATION) { simulation_loop(); }
    else {
        client_order_loop();
    }
    stop();
}

void Exchange::initialize_simulation_books()
{
    for (auto symbols = symbol_manager_->get_all_symbols();
         const auto& symbol : symbols) {
        const auto order_book = symbol_manager_->get_order_book(symbol);
        if (!order_book) continue;

        auto symbol_id = symbol_manager_->get_symbol_id(symbol);
        if (!symbol_id) continue;

        long base_price = 50000;
        Order initial_bid(OrderCore("exchange_mm", *symbol_id), base_price - 200, 1000, true);
        Order initial_ask(OrderCore("exchange_mm", *symbol_id), base_price + 200, 1000, false);

        order_book->AddOrder(initial_bid);
        order_book->AddOrder(initial_ask);

        for (int i = 1; i <= 5; ++i) {
            long bid_price = base_price - 200 - (i * 100);
            long ask_price = base_price + 200 + (i * 100);
            auto quantity = static_cast<uint32_t>(std::abs(quantity_dist_(generator_)));

            Order bid(OrderCore("exchange_mm", *symbol_id), bid_price, quantity, true);
            Order ask(OrderCore("exchange_mm", *symbol_id), ask_price, quantity, false);
            order_book->AddOrder(bid);
            order_book->AddOrder(ask);
        }

        std::cout << "Initialized " << symbol << " with spread: $" << (base_price - 200) / 100.0 << " - $"
                << (base_price + 200) / 100.0 << std::endl;
    }
}

void Exchange::simulation_loop()
{
    const auto symbols = symbol_manager_->get_all_symbols();
    while (running_.load()) {
        if (symbols.empty()) break;

        const auto& symbol = symbols[generator_() % symbols.size()];
        auto order_book = symbol_manager_->get_order_book(symbol);
        auto symbol_id = symbol_manager_->get_symbol_id(symbol);
        if (!order_book || !symbol_id) continue;
        simulate_trading_activity(order_book, *symbol_id, symbol);
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
}

void Exchange::simulate_trading_activity(const std::shared_ptr<OrderBook<mdfeed::MarketDataPublisher> >& order_book,
    const uint32_t symbol_id,
    const std::string_view symbol)
{
    if (bool_dist_(generator_) && bool_dist_(generator_)) {
        place_simulation_market_order(order_book, symbol);
    }
    else {
        add_simulation_limit_order(order_book, symbol_id, symbol);
    }
}

void Exchange::place_simulation_market_order(const std::shared_ptr<OrderBook<mdfeed::MarketDataPublisher> >& order_book,
    const std::string_view symbol)
{
    auto quantity = static_cast<uint32_t>(std::abs(quantity_dist_(generator_)) / 2);
    if (quantity == 0) quantity = 1;
    if (bool_dist_(generator_)) {
        std::cout << "[SIM] " << symbol << " market BUY: " << quantity << std::endl;
        order_book->PlaceMarketBuyOrder(quantity);
    }
    else {
        std::cout << "[SIM] " << symbol << " market SELL: " << quantity << std::endl;
        order_book->PlaceMarketSellOrder(quantity);
    }
}

void Exchange::add_simulation_limit_order(const std::shared_ptr<OrderBook<mdfeed::MarketDataPublisher> >& order_book,
    const uint32_t symbol_id,
    const std::string_view symbol)
{
    auto best_bid = order_book->GetBestBidPrice();
    auto best_ask = order_book->GetBestAskPrice();

    if (!best_bid.has_value() || !best_ask.has_value()) return;

    double mid_price = (best_bid.value() + best_ask.value()) / 2.0;
    bool is_buy = bool_dist_(generator_);

    long price;
    if (is_buy) {
        double target_price = (best_bid.value() + mid_price) / 2.0;
        price = static_cast<long>(std::abs(std::normal_distribution<double>(
            target_price, 200)(generator_)));
    }
    else {
        double target_price = (best_ask.value() + mid_price) / 2.0;
        price = static_cast<long>(std::abs(std::normal_distribution<double>(
            target_price, 200)(generator_)));
    }

    auto quantity = static_cast<uint32_t>(std::abs(quantity_dist_(generator_)));
    if (quantity == 0) quantity = 1;

    const Order order(OrderCore("simulator", symbol_id), price, quantity,
        is_buy);
    order_book->AddOrder(order);

    std::cout << "[SIM] " << symbol << " " << (is_buy ? "BID" : "ASK") << ": "
            << quantity << " @ $" << (price / 100.0) << std::endl;
}

void Exchange::client_order_loop()
{
    orderentry::OrderBuffer order_buffer;

    while (running_.load()) {
        if (order_server_->get_order_buffer()->pop(order_buffer)) {
            process_client_order(order_buffer);
        }
        else {
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
    }
}

void Exchange::process_client_order(const orderentry::OrderBuffer& buffer)
{
    const auto* header = reinterpret_cast<const orderentry::MessageHeader*>(buffer.data);
    try {
        switch (static_cast<orderentry::MessageType>(header->message_type)) {
        case orderentry::MessageType::NEW_ORDER:
            handle_new_order(buffer);
            break;
        case orderentry::MessageType::CANCEL_ORDER:
            handle_cancel_order(buffer);
            break;
        case orderentry::MessageType::MODIFY_ORDER:
            handle_modify_order(buffer);
            break;
        default:
            std::cerr << "Unsupported message type: " << header->message_type << std::endl;
            break;
        }
    }
    catch (const std::exception& e) {
        std::cerr << "Error processing client order: " << e.what() << std::endl;
    }
}

void Exchange::handle_new_order(const orderentry::OrderBuffer& buffer)
{
    const auto* msg = buffer.as<orderentry::NewOrderMessage>();
    const std::string symbol(msg->symbol, strnlen(msg->symbol, sizeof(msg->symbol)));

    std::cout << "[CLIENT] New order: " << msg->client_order_id << " " << symbol
            << " " << (msg->side == orderentry::Side::BUY ? "BUY" : "SELL")
            << " " << msg->quantity << " @ $" << (msg->price / 100.0) << std::endl;

    if (!symbol_manager_->is_valid_symbol(symbol)) {
        std::cerr << "Rejecting order: Unknown symbol " << symbol << std::endl;
        send_order_reject(buffer.client_fd, msg->client_order_id, 1,
            "Unknown symbol");
        return;
    }

    if (msg->quantity == 0 || (msg->order_type == orderentry::OrderType::LIMIT && msg->price == 0)) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 6, "Invalid order");
        return;
    }

    const auto order_book = symbol_manager_->get_order_book(symbol);
    const auto symbol_id = symbol_manager_->get_symbol_id(symbol);

    if (!order_book || !symbol_id) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 2, "Internal error");
        return;
    }

    const std::string username = "client_" + std::to_string(buffer.client_fd);
    const OrderCore core(username, *symbol_id);
    const Order order(core, msg->price, msg->quantity, msg->side == orderentry::Side::BUY);

    // Capture the best resting price before matching removes the level. This
    // permits the incoming client to receive an execution report for the
    // common one-level case; the order-book API does not yet expose a full
    // per-fill execution ledger for reporting every resting counterparty.
    const auto potential_execution_price = msg->side == orderentry::Side::BUY
        ? order_book->GetBestAskPrice()
        : order_book->GetBestBidPrice();

    client_orders_[{buffer.client_fd, msg->client_order_id}] = {
        order.OrderId(), symbol, msg->side};

    send_order_ack(buffer.client_fd, msg->client_order_id, order.OrderId(), symbol);

    const uint64_t old_matched = order_book->GetOrdersMatched();
    order_book->AddOrder(order);

    if (const uint64_t new_matched = order_book->GetOrdersMatched();
        new_matched > old_matched) {
        const uint64_t executed_qty = new_matched - old_matched;
        if (potential_execution_price.has_value()) {
            send_execution_report(buffer.client_fd, msg->client_order_id,
                order.OrderId(), potential_execution_price.value(),
                executed_qty, order.CurrentQuantity(),
                msg->side);
        }
    }
}

void Exchange::handle_cancel_order(const orderentry::OrderBuffer& buffer)
{
    const auto* msg = buffer.as<orderentry::CancelOrderMessage>();

    std::cout << "[CLIENT] Cancel order: " << msg->client_order_id
            << " (original: " << msg->original_client_order_id << ")" << std::endl;

    const auto it = client_orders_.find({buffer.client_fd, msg->original_client_order_id});
    if (it == client_orders_.end()) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 3, "Order not found"); // TODO, use reject enum
        return;
    }

    const long exchange_order_id = it->second.exchange_order_id;
    const std::string symbol(msg->symbol, strnlen(msg->symbol, sizeof(msg->symbol)));

    const auto order_book = symbol_manager_->get_order_book(symbol);
    if (!order_book) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 4, "Symbol not found");
        return;
    }

    try {
        order_book->RemoveOrder(exchange_order_id);

        client_orders_.erase(it);

        send_order_ack(buffer.client_fd, msg->client_order_id, exchange_order_id, symbol);
        std::cout << "Order " << msg->original_client_order_id << " cancelled successfully" << std::endl;
    }
    catch (const std::exception& e) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 5, "Cancel failed");
    }
}

void Exchange::handle_modify_order(const orderentry::OrderBuffer& buffer)
{
    const auto* msg = buffer.as<orderentry::ModifyOrderMessage>();
    const auto original = client_orders_.find({buffer.client_fd, msg->original_client_order_id});
    if (original == client_orders_.end()) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 3, "Order not found");
        return;
    }
    if (msg->new_price == 0 || msg->new_quantity == 0) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 6, "Invalid order");
        return;
    }

    const auto& original_order = original->second;
    const auto order_book = symbol_manager_->get_order_book(original_order.symbol);
    const auto symbol_id = symbol_manager_->get_symbol_id(original_order.symbol);
    if (!order_book || !symbol_id) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 2, "Internal error");
        return;
    }

    try {
        const Order amended(OrderCore("client_" + std::to_string(buffer.client_fd), *symbol_id),
            msg->new_price, msg->new_quantity, original_order.side == orderentry::Side::BUY);
        order_book->AmendOrder(original_order.exchange_order_id, amended);
        client_orders_.erase(original);
        client_orders_[{buffer.client_fd, msg->client_order_id}] = {
            amended.OrderId(), original_order.symbol, original_order.side};
        send_order_ack(buffer.client_fd, msg->client_order_id, amended.OrderId(), original_order.symbol);
    }
    catch (const std::exception&) {
        send_order_reject(buffer.client_fd, msg->client_order_id, 7, "Modify failed");
    }
}

void Exchange::send_order_ack(const int client_fd, const uint64_t client_order_id, const long exchange_order_id,
    const std::string_view symbol)
{
    orderentry::OrderAckMessage ack{};
    orderentry::message_utils::init_header(ack, orderentry::MessageType::ORDER_ACK, 0, 0);
    ack.client_order_id = client_order_id;
    ack.exchange_order_id = exchange_order_id;

    const size_t copy_len = std::min(symbol.length(), sizeof(ack.symbol));
    std::memcpy(ack.symbol, symbol.data(), copy_len);
    if (copy_len < sizeof(ack.symbol)) {
        std::memset(ack.symbol + copy_len, 0, sizeof(ack.symbol) - copy_len);
    }
    std::memset(ack.reserved, 0, sizeof(ack.reserved));
    send_response(client_fd, &ack, sizeof(ack));
}

void Exchange::send_order_reject(const int client_fd, const uint64_t client_order_id, const uint32_t reason,
    const std::string_view text)
{
    orderentry::OrderRejectMessage reject{};
    orderentry::message_utils::init_header(reject, orderentry::MessageType::ORDER_REJECT, 0, 0);
    reject.client_order_id = client_order_id;
    reject.reject_reason = reason;

    const size_t copy_len = std::min(text.length(), sizeof(reject.reject_text));
    std::memcpy(reject.reject_text, text.data(), copy_len);
    if (copy_len < sizeof(reject.reject_text)) {
        std::memset(reject.reject_text + copy_len, 0, sizeof(reject.reject_text) - copy_len);
    }
    std::memset(reject.symbol, 0, sizeof(reject.symbol));
    std::memset(reject.reserved, 0, sizeof(reject.reserved));

    send_response(client_fd, &reject, sizeof(reject));
}

void Exchange::send_execution_report(const int client_fd,
    const uint64_t client_order_id,
    const long exchange_order_id,
    const uint64_t price,
    const uint64_t executed_qty,
    const uint64_t remaining_qty,
    const orderentry::Side side)
{
    orderentry::ExecutionReportMessage exec{};
    orderentry::message_utils::init_header(exec, orderentry::MessageType::EXECUTION_REPORT, 0, 0);
    exec.client_order_id = client_order_id;
    exec.exchange_order_id = exchange_order_id;
    exec.execution_id = exchange_order_id;
    exec.execution_price = price;
    exec.execution_quantity = executed_qty;
    exec.leaves_quantity = remaining_qty;
    exec.side = side;
    std::memset(exec.reserved, 0, sizeof(exec.reserved));

    send_response(client_fd, &exec, sizeof(exec));
}

void Exchange::send_response(const int client_fd, const void* data, const size_t length)
{
    const auto* bytes = static_cast<const uint8_t*>(data);
    size_t offset = 0;
    while (offset < length) {
        const auto sent = send(client_fd, bytes + offset, length - offset, MSG_NOSIGNAL);
        if (sent <= 0) {
            std::cerr << "Failed to send response to client " << client_fd << std::endl;
            return;
        }
        offset += static_cast<size_t>(sent);
    }
}

void Exchange::print_supported_symbols() const
{
    const auto symbols = symbol_manager_->get_all_symbols();
    std::cout << "Supported symbols: ";
    for (size_t i = 0; i < symbols.size(); ++i) {
        std::cout << symbols[i];
        if (i < symbols.size() - 1) std::cout << ", ";
    }
    std::cout << std::endl;
}

void Exchange::print_final_stats() const
{
    const auto stats = multicast_thread_->get_stats();
    uint64_t recovery_connections = 0;
    uint64_t recovery_requests = 0;
    uint64_t recovery_responses = 0;
    uint64_t recovery_messages = 0;
    uint64_t recovery_average_wait = 0;
    uint64_t recovery_max_wait = 0;
    uint64_t recovery_subscribers = 0;
    uint64_t recovery_fairness_jain_ppm = 0;
    std::string recovery_service_distribution{"none"};
    std::cout << "\n=== FINAL EXCHANGE STATISTICS ===" << std::endl;
    std::cout << "Market Data Messages sent: " << stats.messages_sent << std::endl;
    std::cout << "Market Data Send failures: " << stats.send_failures << std::endl;
    std::cout << "Feed A/B successful sends: " << stats.feed_a_sent << '/' << stats.feed_b_sent << std::endl;
    std::cout << "Feed A/B application-layer drops: " << stats.feed_a_app_dropped << '/'
              << stats.feed_b_app_dropped << std::endl;

    if (order_server_) {
        const auto stats = order_server_->get_stats();
        std::cout << "Order Entry Orders received: " << stats.orders_received << std::endl;
        std::cout << "Order Entry Total connections: " << stats.connections << std::endl;
        std::cout << "Order Entry malformed frames: " << stats.malformed_messages << std::endl;
        std::cout << "Order Entry queue overflows: " << stats.queue_overflows << std::endl;
    }

    if (recovery_tcp_server_) {
        const auto recovery = recovery_tcp_server_->stats();
        const auto scheduler = recovery_tcp_server_->scheduler_metrics();
        const auto average_wait = scheduler.recovery_request_count == 0 ? 0
            : scheduler.total_queue_wait_ns / scheduler.recovery_request_count;
        recovery_connections = recovery.connections;
        recovery_requests = recovery.requests_received;
        recovery_responses = recovery.responses_sent;
        recovery_messages = scheduler.messages_recovered;
        recovery_average_wait = average_wait;
        recovery_max_wait = scheduler.max_queue_wait_ns;
        recovery_subscribers = scheduler.per_subscriber_service_distribution.size();
        if (!scheduler.per_subscriber_service_distribution.empty()) {
            std::vector<std::pair<std::string, uint64_t>> served(
                scheduler.per_subscriber_service_distribution.begin(),
                scheduler.per_subscriber_service_distribution.end());
            std::sort(served.begin(), served.end());
            uint64_t total = 0;
            uint64_t sum_of_squares = 0;
            std::ostringstream encoded;
            for (size_t index = 0; index < served.size(); ++index) {
                total += served[index].second;
                sum_of_squares += served[index].second * served[index].second;
                if (index != 0) encoded << ',';
                encoded << served[index].first << ':' << served[index].second;
            }
            recovery_service_distribution = encoded.str();
            // Scheduler-service fairness only; this is not a claim about
            // network delivery fairness. Scale Jain's index for parsing.
            if (sum_of_squares != 0) {
                recovery_fairness_jain_ppm = static_cast<uint64_t>(
                    (static_cast<long double>(total) * total * 1'000'000.0L)
                    / (static_cast<long double>(served.size()) * sum_of_squares));
            }
        }
        std::cout << "Recovery TCP connections: " << recovery.connections << std::endl;
        std::cout << "Recovery TCP requests/responses: " << recovery.requests_received << '/'
                  << recovery.responses_sent << std::endl;
        std::cout << "Recovery TCP malformed/send failures: " << recovery.malformed_frames << '/'
                  << recovery.send_failures << std::endl;
        std::cout << "Recovery scheduler requests/messages: " << scheduler.recovery_request_count << '/'
                  << scheduler.messages_recovered << std::endl;
        std::cout << "Recovery queue wait avg/max ns: " << average_wait << '/'
                  << scheduler.max_queue_wait_ns << std::endl;
        std::cout << "Recovery subscribers served: "
                  << scheduler.per_subscriber_service_distribution.size() << std::endl;
        std::cout << "Recovery service distribution: " << recovery_service_distribution
                  << " (Jain fairness ppm " << recovery_fairness_jain_ppm << ")" << std::endl;
    }

    for (const auto symbols = symbol_manager_->get_all_symbols();
         const auto& symbol : symbols) {
        if (const auto& order_book = symbol_manager_->get_order_book(symbol);
            order_book && order_book->Count() > 0) {
            std::cout << symbol << " - Orders: " << order_book->Count() << ", Matched: " << order_book->
                    GetOrdersMatched() << std::endl;
        }
    }
    std::cout << "EXCHANGE_RESULT md_messages_sent=" << stats.messages_sent
              << " feed_a_sent=" << stats.feed_a_sent
              << " feed_b_sent=" << stats.feed_b_sent
              << " feed_a_app_dropped=" << stats.feed_a_app_dropped
              << " feed_b_app_dropped=" << stats.feed_b_app_dropped
              << " md_send_failures=" << stats.send_failures
              << " recovery_connections=" << recovery_connections
              << " recovery_requests=" << recovery_requests
              << " recovery_responses=" << recovery_responses
              << " recovery_messages=" << recovery_messages
              << " recovery_avg_queue_wait_ns=" << recovery_average_wait
              << " recovery_max_queue_wait_ns=" << recovery_max_wait
              << " recovery_subscribers=" << recovery_subscribers
              << " recovery_fairness_jain_ppm=" << recovery_fairness_jain_ppm
              << " recovery_service_distribution=" << recovery_service_distribution
              << " snapshots_retained=" << snapshot_manager_->snapshot_count()
              << '\n';
}
