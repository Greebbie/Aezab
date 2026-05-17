"""
Aezab platform exception hierarchy.

Centralizes all custom exceptions so that engine modules, API endpoints,
and middleware can catch specific error classes instead of bare Exceptions.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class AezabError(Exception):
    """Base exception for all Aezab platform errors."""

    def __init__(self, message: str = "An unexpected error occurred", detail: dict[str, Any] | None = None) -> None:
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


# ---------------------------------------------------------------------------
# LLM errors
# ---------------------------------------------------------------------------

class LLMError(AezabError):
    """Base exception for LLM-related errors."""

    def __init__(
        self,
        message: str = "LLM error",
        provider: str = "",
        model: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        super().__init__(message, detail)


class LLMTimeoutError(LLMError):
    """LLM request timed out before returning a response."""

    def __init__(
        self,
        message: str = "LLM request timed out",
        provider: str = "",
        model: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider, model, detail)


class LLMRateLimitError(LLMError):
    """LLM provider rate limit has been exceeded."""

    def __init__(
        self,
        message: str = "LLM rate limit exceeded",
        provider: str = "",
        model: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider, model, detail)


class LLMModelError(LLMError):
    """LLM returned an invalid response or encountered a model-level error."""

    def __init__(
        self,
        message: str = "LLM model error",
        provider: str = "",
        model: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider, model, detail)


# ---------------------------------------------------------------------------
# Retrieval errors
# ---------------------------------------------------------------------------

class RetrievalError(AezabError):
    """Base exception for retrieval pipeline errors."""

    def __init__(
        self,
        message: str = "Retrieval error",
        channel: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.channel = channel
        super().__init__(message, detail)


class VectorSearchError(RetrievalError):
    """Vector store search failed."""

    def __init__(
        self,
        message: str = "Vector search failed",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, channel="vector", detail=detail)


class KeywordSearchError(RetrievalError):
    """Keyword / BM25 search failed."""

    def __init__(
        self,
        message: str = "Keyword search failed",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, channel="keyword", detail=detail)


class FastLookupError(RetrievalError):
    """Fast KV channel lookup failed."""

    def __init__(
        self,
        message: str = "Fast lookup failed",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, channel="fast", detail=detail)


# ---------------------------------------------------------------------------
# Workflow errors
# ---------------------------------------------------------------------------

class WorkflowError(AezabError):
    """Base exception for workflow execution errors."""

    def __init__(
        self,
        message: str = "Workflow error",
        workflow_id: str = "",
        step_name: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.step_name = step_name
        super().__init__(message, detail)


class WorkflowValidationError(WorkflowError):
    """Workflow field validation failed."""

    def __init__(
        self,
        message: str = "Workflow validation failed",
        workflow_id: str = "",
        step_name: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, workflow_id, step_name, detail)


class WorkflowStepError(WorkflowError):
    """A workflow step failed during execution."""

    def __init__(
        self,
        message: str = "Workflow step execution failed",
        workflow_id: str = "",
        step_name: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, workflow_id, step_name, detail)


class WorkflowEscalationError(WorkflowError):
    """Escalation handling within a workflow failed."""

    def __init__(
        self,
        message: str = "Workflow escalation failed",
        workflow_id: str = "",
        step_name: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, workflow_id, step_name, detail)


# ---------------------------------------------------------------------------
# Tool invocation errors
# ---------------------------------------------------------------------------

class ToolInvocationError(AezabError):
    """Tool calling failed during agent execution."""

    def __init__(
        self,
        message: str = "Tool invocation failed",
        tool_name: str = "",
        recoverable: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.recoverable = recoverable
        super().__init__(message, detail)


# ---------------------------------------------------------------------------
# Authentication / authorization errors
# ---------------------------------------------------------------------------

class AuthenticationError(AezabError):
    """Base exception for authentication and permission errors."""

    def __init__(
        self,
        message: str = "Authentication error",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail)


class TokenExpiredError(AuthenticationError):
    """Supplied authentication token has expired."""

    def __init__(
        self,
        message: str = "Token has expired",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail)


class InsufficientPermissionError(AuthenticationError):
    """Caller lacks the required permissions for this operation."""

    def __init__(
        self,
        message: str = "Insufficient permissions",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail)
