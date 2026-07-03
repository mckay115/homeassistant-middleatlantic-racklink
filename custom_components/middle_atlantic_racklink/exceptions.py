"""Exceptions for the Middle Atlantic RackLink integration."""

from __future__ import annotations


class RacklinkError(Exception):
    """Base exception for RackLink errors."""


class RacklinkConnectionError(RacklinkError):
    """Raised when the device cannot be reached."""


class RacklinkAuthenticationError(RacklinkError):
    """Raised when the device rejects the provided credentials."""
