"""Inngest integration — client + functions for async document processing,
transactional email delivery, outbound webhooks, and integrations sync."""
from app.services.email.worker import FUNCTIONS as _EMAIL_FUNCTIONS

from .client import get_inngest_client
from .functions import FUNCTIONS as _DOC_FUNCTIONS
from .functions import process_document
from .webhook_functions import FUNCTIONS as _WEBHOOK_FUNCTIONS
from .integration_functions import FUNCTIONS as _INTEGRATION_FUNCTIONS

FUNCTIONS = [
    *_DOC_FUNCTIONS,
    *_EMAIL_FUNCTIONS,
    *_WEBHOOK_FUNCTIONS,
    *_INTEGRATION_FUNCTIONS,
]

__all__ = ["FUNCTIONS", "get_inngest_client", "process_document"]
