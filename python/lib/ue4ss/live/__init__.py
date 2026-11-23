"""Shared live-execution protocol, core, and transport adapters."""

__all__ = ["LiveCore"]


def __getattr__(name):
    if name == "LiveCore":
        from .core import LiveCore
        return LiveCore
    raise AttributeError(name)
