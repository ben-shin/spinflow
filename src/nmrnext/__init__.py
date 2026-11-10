"""Experimental NMR processing with explicit scientific contracts."""
from .model import Axis, Dataset
from .errors import NMRNextError

__version__ = "0.4.0b1"
__all__ = ["Axis", "Dataset", "NMRNextError"]
