"""MCP tool registrations. Each module exposes ``register(server)``."""

from . import assignments, calendar, courses, grades, intelligence, materials, notifications

ALL = (courses, assignments, grades, materials, calendar, notifications, intelligence)


def register_all(server) -> None:
    for module in ALL:
        module.register(server)
