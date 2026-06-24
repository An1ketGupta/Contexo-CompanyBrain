"""Inngest integration — client + functions for async document processing,
transactional email delivery, outbound webhooks, and integrations sync."""
from app.services.email.worker import FUNCTIONS as _EMAIL_FUNCTIONS

from .api_trigger_functions import FUNCTIONS as _API_TRIGGER_FUNCTIONS
from .approval_functions import FUNCTIONS as _APPROVAL_FUNCTIONS
from .archive_functions import FUNCTIONS as _ARCHIVE_FUNCTIONS
from .autoflow_functions import FUNCTIONS as _AUTOFLOW_FUNCTIONS
from .duplicate_detection_functions import FUNCTIONS as _DUPLICATE_DETECTION_FUNCTIONS
from .client import get_inngest_client
from .compliance_functions import FUNCTIONS as _COMPLIANCE_FUNCTIONS
from .embedding_finetune_functions import FUNCTIONS as _EMBEDDING_FT_FUNCTIONS
from .feedback_functions import FUNCTIONS as _FEEDBACK_FUNCTIONS
from .functions import FUNCTIONS as _DOC_FUNCTIONS
from .functions import process_document
from .gmail_functions import FUNCTIONS as _GMAIL_FUNCTIONS
from .integration_functions import FUNCTIONS as _INTEGRATION_FUNCTIONS
from .integration_write_functions import FUNCTIONS as _INTEGRATION_WRITE_FUNCTIONS
from .knowledge_gap_functions import FUNCTIONS as _KNOWLEDGE_GAP_FUNCTIONS
from .meeting_functions import FUNCTIONS as _MEETING_FUNCTIONS
from .onboarding_functions import FUNCTIONS as _ONBOARDING_FUNCTIONS
from .policy_functions import FUNCTIONS as _POLICY_FUNCTIONS
from .query_log_retention import FUNCTIONS as _QUERY_LOG_RETENTION_FUNCTIONS
from .report_functions import FUNCTIONS as _REPORT_FUNCTIONS
from .slack_functions import FUNCTIONS as _SLACK_FUNCTIONS
from .slack_inbound_functions import FUNCTIONS as _SLACK_INBOUND_FUNCTIONS
from .support_functions import FUNCTIONS as _SUPPORT_FUNCTIONS
from .version_diff_functions import FUNCTIONS as _VERSION_DIFF_FUNCTIONS
from .webhook_functions import FUNCTIONS as _WEBHOOK_FUNCTIONS

FUNCTIONS = [
    *_DOC_FUNCTIONS,
    *_EMAIL_FUNCTIONS,
    *_WEBHOOK_FUNCTIONS,
    *_INTEGRATION_FUNCTIONS,
    *_GMAIL_FUNCTIONS,
    *_SLACK_FUNCTIONS,
    *_SLACK_INBOUND_FUNCTIONS,
    *_INTEGRATION_WRITE_FUNCTIONS,
    *_KNOWLEDGE_GAP_FUNCTIONS,
    *_APPROVAL_FUNCTIONS,
    *_ONBOARDING_FUNCTIONS,
    *_POLICY_FUNCTIONS,
    *_COMPLIANCE_FUNCTIONS,
    *_SUPPORT_FUNCTIONS,
    *_MEETING_FUNCTIONS,
    *_API_TRIGGER_FUNCTIONS,
    *_FEEDBACK_FUNCTIONS,
    *_VERSION_DIFF_FUNCTIONS,
    *_ARCHIVE_FUNCTIONS,
    *_QUERY_LOG_RETENTION_FUNCTIONS,
    *_REPORT_FUNCTIONS,
    *_EMBEDDING_FT_FUNCTIONS,
    *_AUTOFLOW_FUNCTIONS,
    *_DUPLICATE_DETECTION_FUNCTIONS,
]

__all__ = ["FUNCTIONS", "get_inngest_client", "process_document"]
