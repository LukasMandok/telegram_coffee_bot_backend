# Coffee Bot Logging Module
# Meaningful logging functions for the Telegram Coffee Bot

import logging
from typing import Optional, Dict, Any
from decimal import Decimal

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)

# Suppress verbose logging from external libraries
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.setLevel(logging.WARNING)

telethon_logger = logging.getLogger("telethon")
telethon_logger.setLevel(logging.WARNING)


# === APPLICATION LIFECYCLE ===

def log_app_startup():
    logger.info("[APP] Coffee Bot application starting up...")

def log_app_shutdown():
    logger.info("[APP] Coffee Bot application shutting down...")
    
def log_telegram_bot_started(api_id: int):
    logger.info("[TELEGRAM] Telegram bot started successfully with API ID: %s", api_id)

# === DATABASE OPERATIONS ===

def log_database_connected(uri: str):
    logger.info("[DB] Successfully connected to MongoDB database: %s", uri)

def log_database_disconnected():
    logger.info("[DB] Successfully disconnected from MongoDB database")
    
def log_setup_database_defaults(admins: Optional[list] = None):
    logger.info("[DB] Default database values have been set up successfully")
    if admins:
        logger.info("[DB] Default admins: %s", admins)

def log_database_connection_failed(error: str):
    logger.error("[DB] Failed to connect to MongoDB database: %s", error)

# === USER AUTHENTICATION & MANAGEMENT ===

def log_user_login_attempt(user_id: int, username: Optional[str] = None):
    if username:
        logger.info("[AUTH] Login attempt for user_id=%s, username=%s", user_id, username)
    else:
        logger.info("[AUTH] Login attempt for user_id=%s", user_id)

def log_user_login_success(user_id: int, username: Optional[str] = None):
    if username:
        logger.info("[AUTH] Successful login for user_id=%s, username=%s", user_id, username)
    else:
        logger.info("[AUTH] Successful login for user_id=%s", user_id)

def log_user_login_failed(user_id: int, reason: str):
    logger.warning("[AUTH] Login failed for user_id=%s, reason: %s", user_id, reason)

def log_user_registration(user_id: int, username: Optional[str] = None):
    if username:
        logger.info("[USER] New user registered: user_id=%s, username=%s", user_id, username)
    else:
        logger.info("[USER] New user registered: user_id=%s", user_id)

def log_admin_verification(user_id: int, is_admin: bool):
    if is_admin:
        logger.info("[AUTH] Admin verification successful for user_id=%s", user_id)
    else:
        logger.warning("[AUTH] Admin verification failed for user_id=%s", user_id)


# === COFFEE CARD MANAGEMENT ===

def log_coffee_card_created(card_name: str, total_coffees: int, purchaser_id: int, cost_per_coffee: Decimal):
    logger.info(
        "[COFFEE] New coffee card created: name='%s', total_coffees=%d, purchaser_id=%s, cost_per_coffee=€%.2f", 
        card_name, total_coffees, purchaser_id, cost_per_coffee
    )

def log_coffee_card_activated(card_id: str, card_name: str):
    logger.info("[COFFEE] Coffee card activated: id=%s, name='%s'", card_id, card_name)

def log_coffee_card_deactivated(card_id: str, card_name: str):
    logger.info("[COFFEE] Coffee card deactivated: id=%s, name='%s'", card_id, card_name)

def log_coffee_card_depleted(card_id: str, card_name: str):
    logger.warning("[COFFEE] Coffee card depleted: id=%s, name='%s'", card_id, card_name)


# === COFFEE ORDERING ===

def log_coffee_order_created(order_id: str, consumer_id: int, initiator_id: int, quantity: int, card_name: str):
    logger.info(
        "[ORDER] Coffee order created: order_id=%s, consumer_id=%s, initiator_id=%s, quantity=%d, card='%s'", 
        order_id, consumer_id, initiator_id, quantity, card_name
    )

def log_coffee_order_failed(consumer_id: int, initiator_id: int, quantity: int, reason: str):
    logger.error(
        "[ORDER] Coffee order failed: consumer_id=%s, initiator_id=%s, quantity=%d, reason: %s", 
        consumer_id, initiator_id, quantity, reason
    )

def log_individual_coffee_order(user_id: int, card_name: str, quantity: int):
    logger.info("[ORDER] Individual coffee order: user_id=%s, card='%s', quantity=%d", user_id, card_name, quantity)



# === GROUP COFFEE SESSIONS ===

def log_coffee_session_started(session_id: str, initiator_id: int, card_names: list):
    logger.info(
        "[SESSION] Coffee session started: session_id=%s, initiator_id=%s, cards=%s", 
        session_id, initiator_id, card_names
    )

def log_coffee_session_participant_added(session_id: str, user_id: int, username: Optional[str] = None):
    if username:
        logger.info("[SESSION] Participant added to session %s: user_id=%s, username=%s", session_id, user_id, username)
    else:
        logger.info("[SESSION] Participant added to session %s: user_id=%s", session_id, user_id)

def log_coffee_session_updated(session_id: str, coffee_counts: Dict[int, int]):
    total_coffees = sum(coffee_counts.values())
    logger.info("[SESSION] Session updated: session_id=%s, total_coffees=%d, participants=%d", 
               session_id, total_coffees, len(coffee_counts))

def log_coffee_session_completed(session_id: str, total_orders: int, total_cost: float):
    logger.info(
        "[SESSION] Coffee session completed: session_id=%s, total_orders=%d, total_cost=€%.2f", 
        session_id, total_orders, total_cost
    )

def log_coffee_session_cancelled(session_id: str, reason: str):
    logger.warning("[SESSION] Coffee session cancelled: session_id=%s, reason: %s", session_id, reason)


# === DEBT MANAGEMENT ===

def log_debt_created(debtor_id: int, creditor_id: int, amount: Decimal, card_name: str):
    logger.info(
        "[DEBT] New debt created: debtor_id=%s owes creditor_id=%s €%.2f for card '%s'", 
        debtor_id, creditor_id, amount, card_name
    )

def log_debt_updated(debt_id: str, old_amount: Decimal, new_amount: Decimal):
    logger.info("[DEBT] Debt updated: debt_id=%s, old_amount=€%.2f, new_amount=€%.2f", debt_id, old_amount, new_amount)

def log_payment_recorded(payment_id: str, payer_id: int, recipient_id: int, amount: Decimal, method: str):
    logger.info(
        "[PAYMENT] Payment recorded: payment_id=%s, payer_id=%s paid recipient_id=%s €%.2f via %s", 
        payment_id, payer_id, recipient_id, amount, method
    )

def log_debt_settled(debt_id: str, amount: Decimal):
    logger.info("[DEBT] Debt settled: debt_id=%s, amount=€%.2f", debt_id, amount)


# === TELEGRAM BOT INTERACTIONS ===

def log_telegram_command(user_id: int, command: str, chat_id: Optional[int] = None):
    if chat_id:
        logger.info("[TELEGRAM] Command received: user_id=%s, command='%s', chat_id=%s", user_id, command, chat_id)
    else:
        logger.info("[TELEGRAM] Command received: user_id=%s, command='%s'", user_id, command)

def log_telegram_callback(user_id: int, callback_data: str, chat_id: Optional[int] = None):
    if chat_id:
        logger.info("[TELEGRAM] Callback received: user_id=%s, callback='%s', chat_id=%s", user_id, callback_data, chat_id)
    else:
        logger.info("[TELEGRAM] Callback received: user_id=%s, callback='%s'", user_id, callback_data)

def log_telegram_message_sent(user_id: int, message_type: str, content_preview: str = ""):
    if content_preview:
        logger.info("[TELEGRAM] Message sent to user_id=%s, type=%s, content='%s...'", user_id, message_type, content_preview[:50])
    else:
        logger.info("[TELEGRAM] Message sent to user_id=%s, type=%s", user_id, message_type)

def log_telegram_keyboard_sent(user_id: int, keyboard_type: str, buttons_count: int):
    logger.info("[TELEGRAM] Keyboard sent to user_id=%s, type=%s, buttons=%d", user_id, keyboard_type, buttons_count)

def log_telegram_message_deleted(message_id: int, user_id: int):
    logger.info("[TELEGRAM] Message deleted: message_id=%s, user_id=%s", message_id, user_id)


# === CONVERSATION MANAGEMENT ===

def log_conversation_started(user_id: int, conversation_type: str):
    logger.info("[CONV] Conversation started: user_id=%s, type=%s", user_id, conversation_type)

def log_conversation_step(user_id: int, conversation_type: str, step: str):
    logger.info("[CONV] Conversation step: user_id=%s, type=%s, step=%s", user_id, conversation_type, step)

def log_conversation_completed(user_id: int, conversation_type: str):
    logger.info("[CONV] Conversation completed: user_id=%s, type=%s", user_id, conversation_type)

def log_conversation_timeout(user_id: int, conversation_type: str, step: str):
    logger.warning("[CONV] Conversation timeout: user_id=%s, type=%s, step=%s", user_id, conversation_type, step)

def log_conversation_cancelled(user_id: int, conversation_type: str, reason: str):
    logger.info("[CONV] Conversation cancelled: user_id=%s, type=%s, reason=%s", user_id, conversation_type, reason)


# === ERROR HANDLING ===

def log_validation_error(operation: str, error: str, context: Optional[Dict[str, Any]] = None):
    if context:
        logger.error("[VALIDATION] %s validation failed: %s, context: %s", operation, error, context)
    else:
        logger.error("[VALIDATION] %s validation failed: %s", operation, error)

def log_database_error(operation: str, error: str, context: Optional[Dict[str, Any]] = None):
    if context:
        logger.error("[DB] Database error during %s: %s, context: %s", operation, error, context)
    else:
        logger.error("[DB] Database error during %s: %s", operation, error)

def log_telegram_api_error(operation: str, error: str, user_id: Optional[int] = None):
    if user_id:
        logger.error("[TELEGRAM] API error during %s for user_id=%s: %s", operation, user_id, error)
    else:
        logger.error("[TELEGRAM] API error during %s: %s", operation, error)

def log_unexpected_error(operation: str, error: str, context: Optional[Dict[str, Any]] = None):
    if context:
        logger.exception("[ERROR] Unexpected error during %s: %s, context: %s", operation, error, context)
    else:
        logger.exception("[ERROR] Unexpected error during %s: %s", operation, error)


# === STATISTICS & MONITORING ===

def log_daily_stats(total_orders: int, total_coffees: int, active_users: int, total_revenue: float):
    logger.info(
        "[STATS] Daily summary: orders=%d, coffees=%d, active_users=%d, revenue=€%.2f", 
        total_orders, total_coffees, active_users, total_revenue
    )

def log_performance_metric(metric_name: str, value: float, unit: str = "ms"):
    logger.info("[PERF] %s: %.2f%s", metric_name, value, unit)

def log_api_request(endpoint: str, method: str, user_id: Optional[int] = None, response_time: Optional[float] = None):
    if user_id and response_time:
        logger.info("[API] %s %s - user_id=%s, response_time=%.2fms", method, endpoint, user_id, response_time)
    elif user_id:
        logger.info("[API] %s %s - user_id=%s", method, endpoint, user_id)
    elif response_time:
        logger.info("[API] %s %s - response_time=%.2fms", method, endpoint, response_time)
    else:
        logger.info("[API] %s %s", method, endpoint)


# === GOOGLE SHEETS INTEGRATION ===

def log_gsheet_sync_started(sheet_name: str):
    logger.info("[GSHEET] Starting sync with Google Sheet: %s", sheet_name)

def log_gsheet_sync_completed(sheet_name: str, records_synced: int):
    logger.info("[GSHEET] Sync completed with Google Sheet: %s, records=%d", sheet_name, records_synced)

def log_gsheet_sync_failed(sheet_name: str, error: str):
    logger.error("[GSHEET] Sync failed with Google Sheet: %s, error: %s", sheet_name, error)

def log_gsheet_api_initialized():
    logger.info("[GSHEET] Google Sheets API initialized successfully")

def log_gsheet_api_initialization_failed(error: str):
    logger.error("[GSHEET] Failed to initialize Google Sheets API: %s", error)

def log_gsheet_worksheet_created(title: str):
    logger.info("[GSHEET] Created new worksheet: %s", title)

def log_gsheet_backup_completed(success_count: int, total_operations: int):
    logger.info("[GSHEET] Backup completed: %d/%d operations successful", success_count, total_operations)

def log_gsheet_backup_failed(error: str):
    logger.error("[GSHEET] Failed to backup all data: %s", error)

def log_gsheet_summary_created():
    logger.info("[GSHEET] Summary sheet created successfully")

def log_gsheet_summary_creation_failed(error: str):
    logger.error("[GSHEET] Failed to create summary sheet: %s", error)

# === AUTHENTICATION & MIDDLEWARE ===

def log_auth_route_bypassed(route: str):
    logger.info("[AUTH] Route '%s' bypassed authentication", route)

def log_auth_token_received():
    logger.info("[AUTH] Authentication token received for validation")