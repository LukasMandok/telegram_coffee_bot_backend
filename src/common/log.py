# Coffee Bot Logging Module
# Meaningful logging functions for the Telegram Coffee Bot

import logging
import sys
import os
import inspect
import ctypes
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

# Global log settings that can be updated at runtime
class LogSettings:
    """Singleton to hold runtime log settings."""
    show_time: bool = True
    show_caller: bool = True
    show_class: bool = True
    level: str = "INFO"
    module_overrides: Dict[str, str] = {}
    
    @classmethod
    def update(cls, show_time: Optional[bool] = None, show_caller: Optional[bool] = None, show_class: Optional[bool] = None, level: Optional[str] = None):
        """Update log settings."""
        if show_time is not None:
            cls.show_time = show_time
        if show_caller is not None:
            cls.show_caller = show_caller
        if show_class is not None:
            cls.show_class = show_class
        if level is not None:
            cls.level = level.upper()

log_settings = LogSettings()


LOG_STATE_SEQUENCE: tuple[str, ...] = (
    "TRACE",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "OFF",
)


LOG_STATE_ICON: dict[str, str] = {
    "OFF": "❌",
    "CRITICAL": "🔴",
    "ERROR": "🟠",
    "WARNING": "🟡",
    "INFO": "🟢",
    "DEBUG": "🔵",
    "TRACE": "🟣",
}


def format_log_state(state: str) -> str:
    normalized = _normalize_log_state(state)
    return f"{normalized} {LOG_STATE_ICON.get(normalized, '')}"


def _normalize_log_state(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in LOG_STATE_SEQUENCE:
        raise ValueError(f"Invalid log state: {value}")
    return v


def _state_to_levelno(state: str) -> int:
    state = _normalize_log_state(state)
    if state == "OFF":
        # No record can ever satisfy this.
        return 10**9
    if state == "TRACE":
        return TRACE_LEVEL
    return getattr(logging, state)


_registered_logger_names: set[str] = set()
_logger_display_names: dict[str, str] = {}


def register_logger(logger_name: str, *, display_name: Optional[str] = None) -> None:
    if logger_name:
        _registered_logger_names.add(logger_name)
        if display_name:
            _logger_display_names[logger_name] = display_name


def get_logger(logger_name: str) -> logging.Logger:
    register_logger(logger_name)
    return logging.getLogger(logger_name)


def get_known_loggers(*, include_external: bool = False) -> list[tuple[str, str]]:
    """Return (logger_name, display_name) pairs."""

    names: set[str] = set(_registered_logger_names)

    for name, entry in logging.Logger.manager.loggerDict.items():
        if not isinstance(name, str):
            continue
        if name == "root":
            continue

        # Skip placeholder nodes created by the logger hierarchy (e.g. "src", "src.bot").
        # These are not real loggers and just clutter the UI.
        if not isinstance(entry, logging.Logger):
            continue

        if include_external:
            names.add(name)
            continue
        if name.startswith("src."):
            names.add(name)

    items: list[tuple[str, str]] = []
    for name in names:
        display = _logger_display_names.get(name)
        if not display:
            # Prefer meaningful module paths for internal loggers.
            # Example: "src.bot.settings_manager" -> "bot.settings_manager".
            display = name[4:] if name.startswith("src.") else name
        items.append((name, display))

    return sorted(items, key=lambda t: (t[1].lower(), t[0].lower()))


def get_known_logger_names(*, include_external: bool = False) -> list[str]:
    return [name for name, _ in get_known_loggers(include_external=include_external)]


class _DynamicLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        override = log_settings.module_overrides.get(record.name)
        if override is not None:
            if override == "OFF":
                return False
            return record.levelno >= _state_to_levelno(override)
        return record.levelno >= _state_to_levelno(log_settings.level)

class BotFormatter(logging.Formatter):
    """Custom formatter that adds caller context and colors for different log levels."""
    
    # Terminal colors (ANSI RGB) — aligned with the project's desired palette.
    # TRACE purple, DEBUG blue, INFO green, WARNING yellow, ERROR orange, CRITICAL red
    _LEVEL_RGB: dict[str, tuple[int, int, int]] = {
        "TRACE": (160, 32, 240),
        "DEBUG": (0, 120, 255),
        "INFO": (0, 200, 0),
        "WARNING": (255, 215, 0),
        "ERROR": (255, 140, 0),
        "CRITICAL": (255, 0, 0),
    }

    _RESET = "\033[0m"

    @staticmethod
    def _colorize_level(level_name: str) -> str:
        rgb = BotFormatter._LEVEL_RGB.get(level_name)
        if rgb is None:
            return level_name
        r, g, b = rgb
        return f"\033[38;2;{r};{g};{b}m{level_name}{BotFormatter._RESET}"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)
    
    def format(self, record):
        # Add color coding if terminal supports it
        if hasattr(sys.stderr, 'isatty') and sys.stderr.isatty():
            levelname_colored = self._colorize_level(record.levelname)
        else:
            levelname_colored = record.levelname
        
        # Build format parts based on settings
        parts = []
        
        # Add time if enabled
        if log_settings.show_time:
            formatted_time = self.formatTime(record, self.datefmt)
            parts.append(formatted_time)
        
        # Add level (always shown)
        parts.append(levelname_colored)
        
        # Add caller info if enabled
        if log_settings.show_caller:
            filename = os.path.basename(record.pathname).replace('.py', '')
            caller_info = f"{filename}:{record.funcName}"
            parts.append(f"[{caller_info}]")
        
        # Add message (always shown)
        parts.append(record.getMessage())
        
        return " - ".join(parts)

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

# Keep runtime global log level in sync with environment defaults.
if log_level in level_map:
    log_settings.level = log_level

# Configure logging format - will show colored level and message
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", 
    level=TRACE_LEVEL,
    datefmt="%H:%M:%S"
)


def _enable_windows_virtual_terminal_processing() -> None:
    """Try to enable ANSI escape sequence handling on Windows consoles."""
    if os.name != "nt":
        return

    # Only attempt if stderr is an interactive terminal.
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        if handle in (0, -1):
            return

        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return

        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        new_mode = ctypes.c_uint32(mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        # If we can't enable it, we silently fall back.
        return

# Always allow records through the root logger; we filter dynamically.
logging.root.setLevel(TRACE_LEVEL)

logger = logging.getLogger(__name__)

_enable_windows_virtual_terminal_processing()

# Apply custom formatter to all handlers with color support
bot_formatter = BotFormatter(datefmt="%H:%M:%S")
for handler in logging.root.handlers:
    handler.setFormatter(bot_formatter)

    # Ensure handlers don't filter out low levels; our filter does.
    handler.setLevel(TRACE_LEVEL)

    # Avoid attaching duplicate filters if this module is imported multiple times.
    if not any(isinstance(f, _DynamicLogFilter) for f in getattr(handler, "filters", [])):
        handler.addFilter(_DynamicLogFilter())

# Suppress verbose logging from external libraries
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.setLevel(logging.WARNING)

telethon_logger = logging.getLogger("telethon")
telethon_logger.setLevel(logging.INFO)

# Suppress MongoDB/PyMongo heartbeat and connection debug messages
pymongo_logger = logging.getLogger("pymongo")
pymongo_logger.setLevel(logging.INFO)

motor_logger = logging.getLogger("motor")
motor_logger.setLevel(logging.WARNING)

# Suppress noisy HTTP client logs (requests/urllib3)
requests_logger = logging.getLogger("requests")
requests_logger.setLevel(logging.WARNING)

urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.WARNING)

urllib3_pool_logger = logging.getLogger("urllib3.connectionpool")
urllib3_pool_logger.setLevel(logging.WARNING)



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
# === LOGGER UTILITY CLASS ===

class Logger:
    """
    Utility logger class that provides color-coded logging with class context.
    
    Example usage:
        logger = Logger("MyClassName")
        logger.info("Something happened", extra_tag="Auth")
        logger.debug("Debug information")
        logger.error("An error occurred", exc=e)
    
    Output format: [LEVEL] [ClassName] [ExtraTag] Message
    Colors: INFO=green, DEBUG=blue, TRACE=gray, WARNING=yellow, ERROR=red
    """
    
    def __init__(self, class_name: Optional[str] = None, *, logger_name: Optional[str] = None):
        """
        Initialize logger with optional class name.
        If class_name is not provided, it will be auto-detected from the caller.
        """
        # Detect caller module for the underlying logger name.
        frame = inspect.currentframe()
        caller_frame = frame.f_back if frame else None
        caller_module_name = (caller_frame.f_globals.get("__name__") if caller_frame else None) or __name__

        if logger_name is None:
            # Default to class-scoped loggers for UI friendliness.
            logger_name = class_name or caller_module_name

        assert logger_name is not None

        if class_name is None:
            # Auto-detect class name from caller
            if caller_frame:
                caller_locals = caller_frame.f_locals
                if 'self' in caller_locals:
                    class_name = caller_locals['self'].__class__.__name__
                elif '__class__' in caller_locals:
                    class_name = caller_locals['__class__'].__name__
                else:
                    class_name = "Unknown"
        
        self.class_name = class_name
        self.logger_name = logger_name
        register_logger(logger_name, display_name=class_name)
        self._logger = get_logger(logger_name)
    
    def _format_message(self, message: str, extra_tag: Optional[str] = None) -> str:
        """Format message with class name and optional extra tag."""
        parts = []
        # Only add class name if enabled in settings
        if log_settings.show_class and self.class_name:
            parts.append(f"[{self.class_name}]")
        if extra_tag:
            parts.append(f"[{extra_tag}]")
        parts.append(message)
        return " ".join(parts)

    @staticmethod
    def _attach_exception(formatted_msg: str, exc: Optional[Exception], kwargs: Dict[str, Any]) -> str:
        if exc:
            formatted_msg += f" - {type(exc).__name__}: {str(exc)}"
            kwargs['exc_info'] = True
        return formatted_msg
    
    def trace(self, message: str, extra_tag: Optional[str] = None, exc: Optional[Exception] = None, **kwargs):
        """Log TRACE level message."""
        formatted_msg = self._format_message(message, extra_tag)
        formatted_msg = self._attach_exception(formatted_msg, exc, kwargs)
        self._logger.log(TRACE_LEVEL, formatted_msg, **kwargs)
    
    def debug(self, message: str, extra_tag: Optional[str] = None, exc: Optional[Exception] = None, **kwargs):
        """Log DEBUG level message."""
        formatted_msg = self._format_message(message, extra_tag)
        formatted_msg = self._attach_exception(formatted_msg, exc, kwargs)
        self._logger.debug(formatted_msg, **kwargs)
    
    def info(self, message: str, extra_tag: Optional[str] = None, exc: Optional[Exception] = None, **kwargs):
        """Log INFO level message."""
        formatted_msg = self._format_message(message, extra_tag)
        formatted_msg = self._attach_exception(formatted_msg, exc, kwargs)
        self._logger.info(formatted_msg, **kwargs)
    
    def warning(self, message: str, extra_tag: Optional[str] = None, exc: Optional[Exception] = None, **kwargs):
        """Log WARNING level message."""
        formatted_msg = self._format_message(message, extra_tag)
        formatted_msg = self._attach_exception(formatted_msg, exc, kwargs)
        self._logger.warning(formatted_msg, **kwargs)
    
    def error(self, message: str, extra_tag: Optional[str] = None, exc: Optional[Exception] = None, **kwargs):
        """Log ERROR level message with optional exception info."""
        formatted_msg = self._format_message(message, extra_tag)
        formatted_msg = self._attach_exception(formatted_msg, exc, kwargs)
        self._logger.error(formatted_msg, **kwargs)
    
    def critical(self, message: str, extra_tag: Optional[str] = None, exc: Optional[Exception] = None, **kwargs):
        """Log CRITICAL level message with optional exception info."""
        formatted_msg = self._format_message(message, extra_tag)
        formatted_msg = self._attach_exception(formatted_msg, exc, kwargs)
        self._logger.critical(formatted_msg, **kwargs)