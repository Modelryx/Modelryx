"""SQLAlchemy persistence models grouped by domain."""

from modelryx.models.actions import AIActionExecution, AIActionProposal
from modelryx.models.approvals import (
    ApprovalReviewerDelegation,
    ApprovalReviewerGroup,
    ApprovalReviewerGroupMember,
    RevisionApprovalDecision,
    RevisionApprovalRequest,
)
from modelryx.models.artifacts import (
    Artifact,
    ArtifactFile,
    ContentBlob,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from modelryx.models.assistant import AiConversation, AiMessage, AiToolExecution
from modelryx.models.attachments import AdditionalFileStage, Attachment, AttachmentCheckout
from modelryx.models.audit import AuditLog
from modelryx.models.bom import BomLine, BomViewRevision, RevisionRelationship
from modelryx.models.cad import CADCommitPlan, CADExecution, CADOperationPlan, CADSession
from modelryx.models.common import utcnow
from modelryx.models.comparisons import (
    RevisionComparison,
    RevisionComparisonChange,
    RevisionComparisonDomain,
    RevisionComparisonExplanation,
)
from modelryx.models.duplicates import DuplicateCandidate, DuplicateCheck, DuplicateFeedback
from modelryx.models.identity import User, UserRole
from modelryx.models.items import Item, ItemNumberTemplate, Part, Revision
from modelryx.models.knowledge import (
    AiDocument,
    AiSearchFeedback,
    AiSearchSession,
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeRelation,
    KnowledgeSource,
)
from modelryx.models.metadata import MetadataExtraction
from modelryx.models.personalization import Favorite, RecentlyViewedItem, UserNotification
from modelryx.models.schema_state import DatabaseSchema
from modelryx.models.staging import StagingFile, StagingSession
from modelryx.models.system import ApplicationSetting, WorkerHeartbeat
from modelryx.models.ui_views import UiSavedView
from modelryx.models.validation import (
    ValidationAIAnalysis,
    ValidationFinding,
    ValidationFindingAction,
    ValidationRun,
)
from modelryx.models.workspace import Checkout, DownloadBundle, ProcessingJob

__all__ = [
    "DatabaseSchema",
    "utcnow",
    "User",
    "UserRole",
    "ApprovalReviewerGroup",
    "ApprovalReviewerGroupMember",
    "ApprovalReviewerDelegation",
    "RevisionApprovalRequest",
    "RevisionApprovalDecision",
    "AuditLog",
    "Item",
    "ItemNumberTemplate",
    "Revision",
    "MetadataExtraction",
    "Artifact",
    "ArtifactFile",
    "ContentBlob",
    "RevisionArtifactRelation",
    "RevisionContentSnapshot",
    "RevisionContentSnapshotArtifact",
    "BomViewRevision",
    "BomLine",
    "RevisionRelationship",
    "Attachment",
    "AdditionalFileStage",
    "AttachmentCheckout",
    "Favorite",
    "RecentlyViewedItem",
    "UserNotification",
    "Checkout",
    "ProcessingJob",
    "DownloadBundle",
    "UiSavedView",
    "AiDocument",
    "AiSearchSession",
    "AiSearchFeedback",
    "KnowledgeSource",
    "KnowledgeChunk",
    "KnowledgeRelation",
    "KnowledgeIndexJob",
    "RevisionComparison",
    "RevisionComparisonDomain",
    "RevisionComparisonChange",
    "RevisionComparisonExplanation",
    "AiConversation",
    "AiMessage",
    "AiToolExecution",
    "DuplicateCheck",
    "DuplicateCandidate",
    "DuplicateFeedback",
    "StagingSession",
    "StagingFile",
    "ValidationRun",
    "ValidationFinding",
    "ValidationAIAnalysis",
    "ValidationFindingAction",
    "AIActionProposal",
    "AIActionExecution",
    "CADSession",
    "CADOperationPlan",
    "CADExecution",
    "CADCommitPlan",
    "ApplicationSetting",
    "WorkerHeartbeat",
    "Part",
]

# Attach PostgreSQL indexes after all model tables have been declared.
from modelryx.models import indexes as _indexes  # noqa: E402,F401
