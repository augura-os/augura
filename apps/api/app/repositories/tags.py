"""Tag / TagAssignment persistence."""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Tag, TagAssignment


def normalize_tag_names(names: Sequence[str]) -> list[str]:
    """Strip, drop empties and de-duplicate while preserving order."""
    seen: set[str] = set()
    normalized: list[str] = []
    for name in names:
        clean = name.strip()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


class TagRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(self, name: str) -> Tag:
        tag = self.db.scalar(select(Tag).where(Tag.name == name))
        if tag is not None:
            return tag
        try:
            with self.db.begin_nested():
                tag = Tag(name=name)
                self.db.add(tag)
                self.db.flush()
        except IntegrityError:
            # 并发 worker 抢先提交同名 tag：pg 唯一索引会阻塞到对方提交后
            # 才报错，此时 RE-SELECT 一定能读到（READ COMMITTED）
            tag = self.db.scalar(select(Tag).where(Tag.name == name))
        return tag

    def get_asset_tags(self, asset_id: str) -> list[Tag]:
        stmt = (
            select(Tag)
            .join(TagAssignment, TagAssignment.tag_id == Tag.id)
            .where(TagAssignment.asset_id == asset_id)
            .order_by(Tag.name)
        )
        return list(self.db.scalars(stmt).all())

    def set_asset_tags(self, asset_id: str, names: Sequence[str]) -> list[Tag]:
        """Replace an asset's tag assignments with exactly ``names``."""
        self.delete_for_asset(asset_id)
        tags: list[Tag] = []
        for name in normalize_tag_names(names):
            tag = self.get_or_create(name)
            self.db.add(TagAssignment(asset_id=asset_id, tag_id=tag.id))
            tags.append(tag)
        self.db.flush()
        self.prune_orphans()
        return tags

    def delete_for_asset(self, asset_id: str) -> None:
        self.db.execute(
            delete(TagAssignment).where(TagAssignment.asset_id == asset_id)
        )
        self.db.flush()

    def prune_orphans(self) -> None:
        """Delete Tag rows no asset references anymore.

        Tag replacement can leave unreferenced rows behind (re-analysis,
        human edits, asset deletion); pruning keeps the dictionary clean so
        stale tags can't resurface in suggestions or the graph.
        """
        self.db.execute(
            delete(Tag).where(~exists().where(TagAssignment.tag_id == Tag.id))
        )
        self.db.flush()
