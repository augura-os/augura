"""Persistence layer — one repository per aggregate."""

from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.performance import PerformanceRepository
from app.repositories.settings import SettingsRepository
from app.repositories.tags import TagRepository

__all__ = [
    "AnalysisRepository",
    "AssetRepository",
    "CreativeRepository",
    "PerformanceRepository",
    "SettingsRepository",
    "TagRepository",
    "VariantRepository",
]
