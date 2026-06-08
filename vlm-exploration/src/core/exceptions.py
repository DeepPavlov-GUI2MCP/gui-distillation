"""
Custom exceptions for the GUI Distillation project.

Hierarchy:
- GUIDistillationError (base)
    ├─ AppError
    │   ├─ AppLaunchError
    │   ├─ AppCloseError
    │   ├─ AppNotRespondingError
    │   └─ AppNotFoundError
    ├─ AutomationError
    │   ├─ ScreenshotError
    │   ├─ ActionExecutionError
    │   ├─ CoordinatesError
    │   ├─ DisplayNotFoundError
    │   └─ PyAutoGUIError
    ├─ TeacherError
    │   ├─ TeacherResponseError
    │   ├─ TeacherTimeoutError
    │   ├─ TeacherParsingError
    │   ├─ TeacherAuthenticationError
    │   └─ TeacherRateLimitError
    ├─ DataError
    │   ├─ DataValidationError
    │   ├─ DatasetBuildError
    │   ├─ SplitError
    │   ├─ LeakageError
    │   ├─ BalanceError
    │   └─ DataFileNotFoundError
    ├─ ConfigError
    │   ├─ ConfigNotFoundError
    │   ├─ InvalidConfigError
    │   └─ MissingConfigError
    ├─ ExplorationError
    │   ├─ ProtocolError
    │   └─ StrategyError
    └─ MCPError
        ├─ ToolNotFoundError
        └─ InvalidRequestError

Phase: 0.3 Infrastructure Setup
"""

from __future__ import annotations

from typing import Optional, Sequence


class GUIDistillationError(Exception):
    """Base exception for all project-specific errors."""


# =========================================================
# 1. APP ERRORS
# =========================================================

class AppError(GUIDistillationError):
    """Base class for application-related errors."""


class AppLaunchError(AppError):
    """Failed to launch the application."""

    def __init__(self, app_name: str, reason: str = ""):
        self.app_name = app_name
        self.reason = reason
        msg = f"Failed to launch '{app_name}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class AppCloseError(AppError):
    """Failed to close the application."""

    def __init__(self, app_name: str, reason: str = ""):
        self.app_name = app_name
        self.reason = reason
        msg = f"Failed to close '{app_name}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class AppNotRespondingError(AppError):
    """Application is not responding (hang/freeze)."""

    def __init__(self, app_name: str, timeout: float):
        self.app_name = app_name
        self.timeout = timeout
        super().__init__(f"'{app_name}' not responding after {timeout:.1f} seconds")


class AppNotFoundError(AppError):
    """Application not found in system."""

    def __init__(self, app_name: str):
        self.app_name = app_name
        super().__init__(f"Application '{app_name}' not found. Is it installed?")


# =========================================================
# 2. AUTOMATION ERRORS
# =========================================================

class AutomationError(GUIDistillationError):
    """Base class for automation-related errors."""


class ScreenshotError(AutomationError):
    """Failed to take or save screenshot."""

    def __init__(self, reason: str = ""):
        self.reason = reason
        msg = "Failed to take screenshot"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class ActionExecutionError(AutomationError):
    """Failed to execute an action (click, type, etc)."""

    def __init__(self, action_type: str, reason: str = ""):
        self.action_type = action_type
        self.reason = reason
        msg = f"Failed to execute action '{action_type}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class CoordinatesError(AutomationError):
    """Invalid coordinates for action."""

    def __init__(self, x: float, y: float, reason: str = "out of bounds"):
        self.x = x
        self.y = y
        self.reason = reason
        super().__init__(f"Invalid coordinates ({x}, {y}): {reason}")


class DisplayNotFoundError(AutomationError):
    """X11 display not found."""

    def __init__(self, display: str = ":99"):
        self.display = display
        super().__init__(f"Display {display} not found. Is Xvfb running?")


class PyAutoGUIError(AutomationError):
    """PyAutoGUI-specific error wrapper."""

    def __init__(self, operation: str, original_error: Exception):
        self.operation = operation
        self.original_error = original_error
        super().__init__(f"PyAutoGUI '{operation}' failed: {original_error}")


# =========================================================
# 3. TEACHER ERRORS
# =========================================================

class TeacherError(GUIDistillationError):
    """Base class for teacher model errors."""


class TeacherResponseError(TeacherError):
    """Teacher returned invalid or empty response."""

    def __init__(self, model: str, status_code: Optional[int] = None):
        self.model = model
        self.status_code = status_code
        msg = f"Teacher '{model}' returned invalid response"
        if status_code is not None:
            msg += f" (status: {status_code})"
        super().__init__(msg)


class TeacherTimeoutError(TeacherError):
    """Teacher request timed out."""

    def __init__(self, model: str, timeout: float):
        self.model = model
        self.timeout = timeout
        super().__init__(f"Teacher '{model}' timeout after {timeout:.1f}s")


class TeacherParsingError(TeacherError):
    """Failed to parse teacher response (JSON)."""

    def __init__(self, model: str, raw_response: str, parser_error: str, max_preview: int = 200):
        self.model = model
        self.parser_error = parser_error
        self.raw_response_preview = (raw_response or "")[:max_preview]
        super().__init__(
            f"Failed to parse '{model}' response: {parser_error}\n"
            f"Raw preview: {self.raw_response_preview}..."
        )


class TeacherAuthenticationError(TeacherError):
    """API key invalid or expired."""

    def __init__(self, model: str):
        self.model = model
        super().__init__(f"Authentication failed for '{model}'. Check API key.")


class TeacherRateLimitError(TeacherError):
    """Rate limit exceeded."""

    def __init__(self, model: str, retry_after: Optional[int] = None):
        self.model = model
        self.retry_after = retry_after
        msg = f"Rate limit exceeded for '{model}'"
        if retry_after is not None:
            msg += f". Retry after {retry_after}s"
        super().__init__(msg)


# =========================================================
# 4. DATA ERRORS
# =========================================================

class DataError(GUIDistillationError):
    """Base class for data-related errors."""


class DataValidationError(DataError):
    """Data validation failed."""

    def __init__(self, schema: str, errors: str):
        self.schema = schema
        self.errors = errors
        super().__init__(f"Validation failed for {schema}: {errors}")


class DatasetBuildError(DataError):
    """Failed to build dataset from raw data."""

    def __init__(self, dataset_name: str, reason: str):
        self.dataset_name = dataset_name
        self.reason = reason
        super().__init__(f"Failed to build '{dataset_name}' dataset: {reason}")


class SplitError(DataError):
    """Error during train/val/test split."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Split failed: {reason}")


class LeakageError(DataError):
    """Data leakage detected between splits."""

    def __init__(self, screen_ids: Sequence[str]):
        self.screen_ids = list(screen_ids)
        super().__init__(f"Data leakage detected! Same screen IDs in multiple splits: {self.screen_ids}")


class BalanceError(DataError):
    """Dataset imbalance beyond acceptable threshold."""

    def __init__(self, class_name: str, count: int, threshold: int):
        self.class_name = class_name
        self.count = count
        self.threshold = threshold
        super().__init__(f"Class '{class_name}' has only {count} examples, need at least {threshold}")


class DataFileNotFoundError(DataError):
    """Required data file not found."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Required file not found: {path}")


# =========================================================
# 5. CONFIG ERRORS
# =========================================================

class ConfigError(GUIDistillationError):
    """Base class for configuration errors."""


class ConfigNotFoundError(ConfigError):
    """Configuration file not found."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        super().__init__(f"Configuration file not found: {config_path}")


class InvalidConfigError(ConfigError):
    """Invalid configuration (wrong format, missing fields)."""

    def __init__(self, config_path: str, errors: str):
        self.config_path = config_path
        self.errors = errors
        super().__init__(f"Invalid configuration in {config_path}: {errors}")


class MissingConfigError(ConfigError):
    """Required configuration parameter missing."""

    def __init__(self, param: str):
        self.param = param
        super().__init__(f"Missing required configuration parameter: {param}")


# =========================================================
# 6. EXPLORATION ERRORS
# =========================================================

class ExplorationError(GUIDistillationError):
    """Base class for exploration errors."""


class ProtocolError(ExplorationError):
    """Error in exploration protocol."""

    def __init__(self, step: str, reason: str):
        self.step = step
        self.reason = reason
        super().__init__(f"Exploration protocol failed at '{step}': {reason}")


class StrategyError(ExplorationError):
    """Error in action selection strategy."""

    def __init__(self, strategy: str, reason: str):
        self.strategy = strategy
        self.reason = reason
        super().__init__(f"Strategy '{strategy}' failed: {reason}")


# =========================================================
# 7. MCP SERVER ERRORS
# =========================================================

class MCPError(GUIDistillationError):
    """Base class for MCP server errors."""


class ToolNotFoundError(MCPError):
    """Requested tool not found."""

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Tool not found: {tool_name}")


class InvalidRequestError(MCPError):
    """Invalid MCP request format."""

    def __init__(self, details: str):
        self.details = details
        super().__init__(f"Invalid MCP request: {details}")


# =========================================================
# 8. HELPER
# =========================================================

def format_exception(e: Exception, context: str = "") -> str:
    """
    Produce a consistent, user-friendly error message for logs/CLI.

    Note: keep this lightweight; logging configuration should live elsewhere.
    """
    if isinstance(e, GUIDistillationError):
        msg = str(e)
    else:
        msg = f"{e.__class__.__name__}: {e}"

    return f"[{context}] {msg}" if context else msg
