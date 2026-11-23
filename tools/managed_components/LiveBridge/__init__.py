"""Thin managed entry for the canonical packaged live bridge implementation."""


def start(ctx):
    from ue4ss.live.component import start as start_live

    start_live(ctx)
