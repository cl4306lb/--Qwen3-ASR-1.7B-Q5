"""Reusable local speech recognition; importing this package never opens a microphone."""
from .engine import ASRClient, TranscriptionResult, TranscriptionCancelled

__all__ = ["ASRClient", "TranscriptionResult", "TranscriptionCancelled"]
__version__ = "1.0.0"
