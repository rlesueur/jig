"""Container sandbox backend (Docker): code, shell and the headless browser in a hardened container."""

from .backend import BrowserSession, ContainerSandbox
from .egress import EgressBlocked, EgressProxy

__all__ = ["BrowserSession", "ContainerSandbox", "EgressBlocked", "EgressProxy"]
