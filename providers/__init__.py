"""Extensible first-party CI provider adapters.

An adapter exposes ``login()``, ``authenticated()``, and ``prepare()``.
``prepare`` returns a Registration object containing a short-lived runner secret
that is never serialized by the hub (except CircleCI's persistent agent token).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Registration:
    name: str
    target: str
    token: str
    metadata: dict[str, str]

class Adapter(Protocol):
    def login(self, host: str | None = None) -> None: ...
    def authenticated(self, host: str | None = None) -> bool: ...
    def prepare(self) -> Registration: ...

from . import github, gitlab, circleci

ADAPTERS: dict[str, Adapter] = {
    'github': github,
    'gitlab': gitlab,
    'circleci': circleci,
}
