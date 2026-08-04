"""Function-calling tool declarations and dispatch."""

from tools.declarations import TOOLS
from tools.dispatch import dispatch_tool

__all__ = ["TOOLS", "dispatch_tool"]
