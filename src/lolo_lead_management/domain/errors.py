from __future__ import annotations


class LeadManagementError(Exception):
    """Base error for the engine."""


class InvalidAgentOutputError(LeadManagementError):
    """Raised when an agent returns malformed JSON."""


class PersistenceError(LeadManagementError):
    """Raised when persistence fails."""
