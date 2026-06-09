"""Inngest integration — client + functions for async document processing
and transactional email delivery."""
from app.services.email.worker import FUNCTIONS as _EMAIL_FUNCTIONS

from .client import get_inngest_client
from .functions import FUNCTIONS as _DOC_FUNCTIONS
from .functions import process_document

FUNCTIONS = [*_DOC_FUNCTIONS, *_EMAIL_FUNCTIONS]

__all__ = ["FUNCTIONS", "get_inngest_client", "process_document"]
