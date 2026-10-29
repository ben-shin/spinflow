"""Experimental NMR processing with explicit scientific contracts."""
from .model import Axis, Dataset
from .errors import NMRNextError

__version__ = "0.2.0"
__all__ = ["Axis", "Dataset", "NMRNextError"]
