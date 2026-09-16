"""All ORM models — importing this package registers every table on Base."""

from app.models.asset import AnalysisResult, CreativeAsset, Performance
from app.models.base import Base, TimestampMixin, new_uuid
from app.models.creative import Creative, CreativeVariant
from app.models.derivation import VariantDerivation
from app.models.dna import CreativeDNA
from app.models.edit_log import EditLog
from app.models.graph import GraphEdge, GraphNode
from app.models.judge import JudgeSuggestion
from app.models.project import Project
from app.models.ruling import SplitRuling
from app.models.setting import Setting
from app.models.tag import Tag, TagAssignment

__all__ = [
    "AnalysisResult",
    "Base",
    "Creative",
    "CreativeAsset",
    "CreativeDNA",
    "CreativeVariant",
    "EditLog",
    "GraphEdge",
    "GraphNode",
    "JudgeSuggestion",
    "Performance",
    "Project",
    "Setting",
    "SplitRuling",
    "Tag",
    "TagAssignment",
    "TimestampMixin",
    "VariantDerivation",
    "new_uuid",
]
