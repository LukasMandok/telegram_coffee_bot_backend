# Coffee Bot Logging Module
# Meaningful logging functions for the Telegram Coffee Bot

import logging
import sys
import os
from typing import Optional, Dict, Any

# Simple request context tracking
import uuid
from contextvars import ContextVar


# Custom TRACE level (lower than DEBUG)
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

# Convenience constants for log levels
TRACE = TRACE_LEVEL
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL

class BotFormatter(logging.Formatter):
    """Custom formatter that adds caller context and colors for different log levels."""
    
    # Color codes for different log levels
    COLORS = {
        'TRACE': '\033[90m',    # Dark gray
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[0m',      # Default
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[91m', # Bright red
        'RESET': '\033[0m'      # Reset color
    }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)
    
    def format(self, record):
        # Add caller context (file:function:line)
        if hasattr(record, 'pathname') and hasattr(record, 'funcName'):
            filename = record.pathname.split('/')[-1].split('\\')[-1]
            record.caller_context = f"{filename}:{record.funcName}:{record.lineno}"
        else:
            record.caller_context = "unknown"
        
        # Add color coding if terminal supports it
        if hasattr(sys.stderr, 'isatty') and sys.stderr.isatty():
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            record.levelname = f"{color}{record.levelname}{self.COLORS['RESET']}"
        
        return super().format(record)
    
    def trace(self, message: str, **kwargs):
        """Log trace level message with automatic context."""
        context = get_context_suffix()
        if kwargs:
            params = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            self.logger.log(TRACE_LEVEL, f"[TRACE] {message} | {params}{context}")
        else:
            self.logger.log(TRACE_LEVEL, f"[TRACE] {message}{context}")
    
    def debug(self, message: str, **kwargs):
        """Log debug level message with automatic context."""
        context = get_context_suffix()
        if kwargs:
            params = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            self.logger.debug(f"[DEBUG] {message} | {params}{context}")
        else:
            self.logger.debug(f"[DEBUG] {message}{context}")
    
    def info(self, message: str, **kwargs):
        """Log info level message with automatic context."""
        context = get_context_suffix()
        if kwargs:
            params = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            self.logger.info(f"[INFO] {message} | {params}{context}")
        else:
            self.logger.info(f"[INFO] {message}{context}")
    
    def warning(self, message: str, **kwargs):
        """Log warning level message with automatic context."""
        context = get_context_suffix()
        if kwargs:
            params = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            self.logger.warning(f"[WARNING] {message} | {params}{context}")
        else:
            self.logger.warning(f"[WARNING] {message}{context}")
    
    def error(self, message: str, **kwargs):
        """Log error level message with automatic context."""
        context = get_context_suffix()
        if kwargs:
            params = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            self.logger.error(f"[ERROR] {message} | {params}{context}")
        else:
            self.logger.error(f"[ERROR] {message}{context}")

# Get log level from environment variable or default to INFO
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
level_map = {
    'TRACE': TRACE_LEVEL,
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(caller_context)s - %(message)s", 
    level=level_map.get(log_level, logging.INFO)
)

logger = logging.getLogger(__name__)

# Create a global formatter instance with logging methods
bot_formatter = BotFormatter()

# Apply custom formatter to all handlers
for handler in logging.root.handlers:
    handler.setFormatter(bot_formatter)

# Suppress verbose logging from external libraries
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.setLevel(logging.WARNING)

telethon_logger = logging.getLogger("telethon")
telethon_logger.setLevel(logging.WARNING)



request_id: ContextVar[Optional[str]] = ContextVar('request_id', default=None)
current_user_id: ContextVar[Optional[int]] = ContextVar('current_user_id', default=None)

def set_request_context(user_id: Optional[int] = None, req_id: Optional[str] = None):
    """Set context for request tracking - call this at the start of API requests."""
    current_user_id.set(user_id)
    request_id.set(req_id or str(uuid.uuid4())[:8])

def get_context_suffix() -> str:
    """Get current context info to append to log messages."""
    req_id = request_id.get()
    user_id = current_user_id.get()
    if req_id or user_id:
        parts = []
        if req_id:
            parts.append(f"req_id={req_id}")
        if user_id:
            parts.append(f"user_id={user_id}")
        return f" | {', '.join(parts)}"
    return ""


# === APPLICATION LIFECYCLE ===

def log_app_startup(level: int = logging.INFO):
    logger.log(level, "[APP] Coffee Bot application starting up...")

def log_app_shutdown(level: int = logging.INFO):
    logger.log(level, "[APP] Coffee Bot application shutting down...")
    
def log_telegram_bot_started(api_id: int, level: int = logging.INFO):
    logger.log(level, "[TELEGRAM] Telegram bot started successfully with API ID: %s", api_id)

# === DATABASE OPERATIONS ===

def log_database_connected(uri: str, level: int = logging.INFO):
    logger.log(level, "[DB] Successfully connected to MongoDB database: %s", uri)

def log_database_disconnected(level: int = logging.INFO):
    logger.log(level, "[DB] Successfully disconnected from MongoDB database")
    
def log_setup_database_defaults(admins: Optional[list] = None, level: int = logging.INFO):
    logger.log(level, "[DB] Default database values have been set up successfully")
    if admins:
        logger.log(level, "[DB] Default admins: %s", admins)

def log_database_connection_failed(error: str, level: int = logging.ERROR):
    logger.log(level, "[DB] Failed to connect to MongoDB database: %s", error)

# === USER AUTHENTICATION & MANAGEMENT ===

def log_user_login_attempt(user_id: int, username: Optional[str] = None, level: int = logging.INFO):
    context = get_context_suffix()
    if username:
        logger.log(level, "[AUTH] Login attempt for user_id=%s, username=%s%s", user_id, username, context)
    else:
        logger.log(level, "[AUTH] Login attempt for user_id=%s%s", user_id, context)

def log_user_login_success(user_id: int, username: Optional[str] = None, level: int = logging.INFO):
    context = get_context_suffix()
    if username:
        logger.log(level, "[AUTH] Successful login for user_id=%s, username=%s%s", user_id, username, context)
    else:
        logger.log(level, "[AUTH] Successful login for user_id=%s%s", user_id, context)

def log_user_login_failed(user_id: int, reason: str, level: int = logging.WARNING):
    context = get_context_suffix()
    logger.log(level, "[AUTH] Login failed for user_id=%s, reason: %s%s", user_id, reason, context)

def log_user_registration(user_id: int, username: Optional[str] = None, level: int = logging.INFO):
    if username:
        logger.log(level, "[USER] New user registered: user_id=%s, username=%s", user_id, username)
    else:
        logger.log(level, "[USER] New user registered: user_id=%s", user_id)

def log_admin_verification(user_id: int, is_admin: bool, level: Optional[int] = None):
    # Use different default levels based on result
    if level is None:
        level = logging.INFO if is_admin else logging.WARNING
    
    if is_admin:
        logger.log(level, "[AUTH] Admin verification successful for user_id=%s", user_id)
    else:
        logger.log(level, "[AUTH] Admin verification failed for user_id=%s", user_id)


# === COFFEE CARD MANAGEMENT ===

def log_coffee_card_created(card_name: str, total_coffees: int, purchaser_id: int, cost_per_coffee: float, level: int = logging.INFO):
    logger.log(level,
        "[COFFEE] New coffee card created: name='%s', total_coffees=%d, purchaser_id=%s, cost_per_coffee=€%.2f", 
        card_name, total_coffees, purchaser_id, cost_per_coffee
    )

def log_coffee_card_activated(card_id: str, card_name: str, level: int = logging.INFO):
    logger.log(level, "[COFFEE] Coffee card activated: id=%s, name='%s'", card_id, card_name)

def log_coffee_card_deactivated(card_id: str, card_name: str, level: int = logging.INFO):
    logger.log(level, "[COFFEE] Coffee card deactivated: id=%s, name='%s'", card_id, card_name)

def log_coffee_card_depleted(card_id: str, card_name: str, level: int = logging.WARNING):
    logger.log(level, "[COFFEE] Coffee card depleted: id=%s, name='%s'", card_id, card_name)


# === COFFEE ORDERING ===

def log_coffee_order_created(order_id: str, consumer_id: int, initiator_id: int, quantity: int, card_name: str, level: int = logging.INFO):
    logger.log(level,
        "[ORDER] Coffee order created: order_id=%s, consumer_id=%s, initiator_id=%s, quantity=%d, card='%s'", 
        order_id, consumer_id, initiator_id, quantity, card_name
    )

def log_coffee_order_failed(consumer_id: int, initiator_id: int, quantity: int, reason: str, level: int = logging.ERROR):
    logger.log(level,
        "[ORDER] Coffee order failed: consumer_id=%s, initiator_id=%s, quantity=%d, reason: %s", 
        consumer_id, initiator_id, quantity, reason
    )

def log_individual_coffee_order(user_id: int, card_name: str, quantity: int, level: int = logging.INFO):
    logger.log(level, "[ORDER] Individual coffee order: user_id=%s, card='%s', quantity=%d", user_id, card_name, quantity)



# === GROUP COFFEE SESSIONS ===

def log_coffee_session_started(session_id: str, initiator_id: int, card_names: list, level: int = logging.INFO):
    logger.log(level,
        "[SESSION] Coffee session started: session_id=%s, initiator_id=%s, cards=%s", 
        session_id, initiator_id, card_names
    )

def log_coffee_session_participant_added(session_id: str, user_id: int, successful: bool = True, level: int = logging.INFO):
    if successful:
        logger.log(level, "[SESSION] Participant added to session %s: user_id=%s", session_id, user_id)
    else:
        logger.log(level, "[SESSION] Participant was already part of the session %s: user_id=%s", session_id, user_id)

def log_coffee_session_participant_removed(session_id: str, user_id: int, successful: bool = True, level: int = logging.INFO):
    if successful:
        logger.log(level, "[SESSION] Participant removed from session %s: user_id=%s", session_id, user_id)
    else:
        logger.log(level, "[SESSION] Participant was not part of the session %s: user_id=%s", session_id, user_id)
    
def log_coffee_session_updated(session_id: str, coffee_counts: Dict[int, int], level: int = logging.INFO):
    total_coffees = sum(coffee_counts.values())
    logger.log(level, "[SESSION] Session updated: session_id=%s, total_coffees=%d, participants=%d", 
               session_id, total_coffees, len(coffee_counts))

def log_coffee_session_completed(session_id: str, total_orders: int, total_cost: float, level: int = logging.INFO):
    logger.log(level,
        "[SESSION] Coffee session completed: session_id=%s, total_orders=%d, total_cost=€%.2f", 
        session_id, total_orders, total_cost
    )

def log_coffee_session_cancelled(session_id: str, reason: str, level: int = logging.WARNING):
    logger.log(level, "[SESSION] Coffee session cancelled: session_id=%s, reason: %s", session_id, reason)


# === DEBT MANAGEMENT ===

def log_debt_created(debtor_id: int, creditor_id: int, amount: float, card_name: str, level: int = logging.INFO):
    logger.log(level,
        "[DEBT] New debt created: debtor_id=%s owes creditor_id=%s €%.2f for card '%s'", 
        debtor_id, creditor_id, amount, card_name
    )

def log_debt_updated(debt_id: str, old_amount: float, new_amount: float, level: int = logging.INFO):
    logger.log(level, "[DEBT] Debt updated: debt_id=%s, old_amount=€%.2f, new_amount=€%.2f", debt_id, old_amount, new_amount)

def log_payment_recorded(payment_id: str, payer_id: int, recipient_id: int, amount: float, method: str, level: int = logging.INFO):
    logger.log(level,
        "[PAYMENT] Payment recorded: payment_id=%s, payer_id=%s paid recipient_id=%s €%.2f via %s", 
        payment_id, payer_id, recipient_id, amount, method
    )

def log_debt_settled(debt_id: str, amount: float, level: int = logging.INFO):
    logger.log(level, "[DEBT] Debt settled: debt_id=%s, amount=€%.2f", debt_id, amount)


# === TELEGRAM BOT INTERACTIONS ===

def log_telegram_command(user_id: int, command: str, chat_id: Optional[int] = None, level: int = logging.INFO):
    if chat_id:
        logger.log(level, "[TELEGRAM] Command received: user_id=%s, command='%s', chat_id=%s", user_id, command, chat_id)
    else:
        logger.log(level, "[TELEGRAM] Command received: user_id=%s, command='%s'", user_id, command)

def log_telegram_callback(user_id: int, callback_data: str, chat_id: Optional[int] = None, level: int = logging.INFO):
    if chat_id:
        logger.log(level, "[TELEGRAM] Callback received: user_id=%s, callback='%s', chat_id=%s", user_id, callback_data, chat_id)
    else:
        logger.log(level, "[TELEGRAM] Callback received: user_id=%s, callback='%s'", user_id, callback_data)

def log_telegram_message_sent(user_id: int, message_type: str, content_preview: str = "", level: int = logging.INFO):
    if content_preview:
        logger.log(level, "[TELEGRAM] Message sent to user_id=%s, type=%s, content='%s...'", user_id, message_type, content_preview[:50])
    else:
        logger.log(level, "[TELEGRAM] Message sent to user_id=%s, type=%s", user_id, message_type)

def log_telegram_keyboard_sent(user_id: int, keyboard_type: str, buttons_count: int, level: int = logging.INFO):
    logger.log(level, "[TELEGRAM] Keyboard sent to user_id=%s, type=%s, buttons=%d", user_id, keyboard_type, buttons_count)

def log_telegram_message_deleted(message_id: int, user_id: int, level: int = logging.INFO):
    logger.log(level, "[TELEGRAM] Message deleted: message_id=%s, user_id=%s", message_id, user_id)


# === CONVERSATION MANAGEMENT ===

def log_conversation_started(user_id: int, conversation_type: str, level: int = logging.INFO):
    logger.log(level, "[CONV] Conversation started: user_id=%s, type=%s", user_id, conversation_type)

def log_conversation_step(user_id: int, conversation_type: str, step: str, level: int = logging.INFO):
    logger.log(level, "[CONV] Conversation step: user_id=%s, type=%s, step=%s", user_id, conversation_type, step)

def log_conversation_completed(user_id: int, conversation_type: str, level: int = logging.INFO):
    logger.log(level, "[CONV] Conversation completed: user_id=%s, type=%s", user_id, conversation_type)

def log_conversation_timeout(user_id: int, conversation_type: str, step: str, level: int = logging.WARNING):
    logger.log(level, "[CONV] Conversation timeout: user_id=%s, type=%s, step=%s", user_id, conversation_type, step)

def log_conversation_cancelled(user_id: int, conversation_type: str, reason: str, level: int = logging.INFO):
    logger.log(level, "[CONV] Conversation cancelled: user_id=%s, type=%s, reason=%s", user_id, conversation_type, reason)


# === ERROR HANDLING ===

def log_validation_error(operation: str, error: str, context: Optional[Dict[str, Any]] = None, level: int = logging.ERROR):
    if context:
        logger.log(level, "[VALIDATION] %s validation failed: %s, context: %s", operation, error, context)
    else:
        logger.log(level, "[VALIDATION] %s validation failed: %s", operation, error)

def log_database_error(operation: str, error: str, context: Optional[Dict[str, Any]] = None, level: int = logging.ERROR):
    if context:
        logger.log(level, "[DB] Database error during %s: %s, context: %s", operation, error, context)
    else:
        logger.log(level, "[DB] Database error during %s: %s", operation, error)

def log_telegram_api_error(operation: str, error: str, user_id: Optional[int] = None, level: int = logging.ERROR):
    if user_id:
        logger.log(level, "[TELEGRAM] API error during %s for user_id=%s: %s", operation, user_id, error)
    else:
        logger.log(level, "[TELEGRAM] API error during %s: %s", operation, error)

def log_unexpected_error(operation: str, error: str, context: Optional[Dict[str, Any]] = None, level: int = logging.ERROR):
    if context:
        # Use logger.log with exc_info=True to include stack trace when logging exceptions
        logger.log(level, "[ERROR] Unexpected error during %s: %s, context: %s", operation, error, context, exc_info=True)
    else:
        logger.log(level, "[ERROR] Unexpected error during %s: %s", operation, error, exc_info=True)


# === STATISTICS & MONITORING ===

def log_daily_stats(total_orders: int, total_coffees: int, active_users: int, total_revenue: float, level: int = logging.INFO):
    logger.log(level,
        "[STATS] Daily summary: orders=%d, coffees=%d, active_users=%d, revenue=€%.2f", 
        total_orders, total_coffees, active_users, total_revenue
    )

def log_performance_metric(metric_name: str, value: float, unit: str = "ms", level: int = logging.INFO):
    logger.log(level, "[PERF] %s: %.2f%s", metric_name, value, unit)

def log_api_request(endpoint: str, method: str, user_id: Optional[int] = None, response_time: Optional[float] = None, level: int = logging.INFO):
    if user_id and response_time:
        logger.log(level, "[API] %s %s - user_id=%s, response_time=%.2fms", method, endpoint, user_id, response_time)
    elif user_id:
        logger.log(level, "[API] %s %s - user_id=%s", method, endpoint, user_id)
    elif response_time:
        logger.log(level, "[API] %s %s - response_time=%.2fms", method, endpoint, response_time)
    else:
        logger.log(level, "[API] %s %s", method, endpoint)


# === GOOGLE SHEETS INTEGRATION ===

def log_gsheet_sync_started(sheet_name: str, level: int = logging.INFO):
    logger.log(level, "[GSHEET] Starting sync with Google Sheet: %s", sheet_name)

def log_gsheet_sync_completed(sheet_name: str, records_synced: int, level: int = logging.INFO):
    logger.log(level, "[GSHEET] Sync completed with Google Sheet: %s, records=%d", sheet_name, records_synced)

def log_gsheet_sync_failed(sheet_name: str, error: str, level: int = logging.ERROR):
    logger.log(level, "[GSHEET] Sync failed with Google Sheet: %s, error: %s", sheet_name, error)

def log_gsheet_api_initialized(level: int = logging.INFO):
    logger.log(level, "[GSHEET] Google Sheets API initialized successfully")

def log_gsheet_api_initialization_failed(error: str, level: int = logging.ERROR):
    logger.log(level, "[GSHEET] Failed to initialize Google Sheets API: %s", error)

def log_gsheet_worksheet_created(title: str, level: int = logging.INFO):
    logger.log(level, "[GSHEET] Created new worksheet: %s", title)

def log_gsheet_backup_completed(success_count: int, total_operations: int, level: int = logging.INFO):
    logger.log(level, "[GSHEET] Backup completed: %d/%d operations successful", success_count, total_operations)

def log_gsheet_backup_failed(error: str, level: int = logging.ERROR):
    logger.log(level, "[GSHEET] Failed to backup all data: %s", error)

def log_gsheet_summary_created(level: int = logging.INFO):
    logger.log(level, "[GSHEET] Summary sheet created successfully")

def log_gsheet_summary_creation_failed(error: str, level: int = logging.ERROR):
    logger.log(level, "[GSHEET] Failed to create summary sheet: %s", error)

# === AUTHENTICATION & MIDDLEWARE ===

def log_auth_route_bypassed(route: str, level: int = logging.INFO):
    logger.log(level, "[AUTH] Route '%s' bypassed authentication", route)

def log_auth_token_received(level: int = logging.INFO):
    logger.log(level, "[AUTH] Authentication token received for validation")