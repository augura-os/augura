"""合并执行层：POST /graph/merge 路由与 judge_pipeline 自动合并共用。

从 graph 路由抽出的合并执行体（守卫校验 → 移动变体 → 删 source →
edit_logs → Neo4j 同步 → 重建镜像），两个人口共享同一份语义：

- 人工（auto=False）：merge_guard 有 block 时必须带 force_reason（推翻
  既定裁决要留理由，案例 13 教训）
- 自动（auto=True）：merge_guard 有 block 一律不执行（抛 MergeBlocked，
  无 force 概念）；edit_logs 的 new_value 带 ``auto:`` 前缀
  （judge_calibration 的改判率统计依赖这个前缀识别自动判定）
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import ApiError
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.services import graph_sync
from app.services.merge_guard import GuardHit, check_merge

logger = logging.getLogger(__name__)


class MergeBlocked(Exception):
    """自动合并被 merge_guard 拦截（调用方按"只写建议"降级处理）。"""


def merge_creatives(
    db: Session,
    settings: Settings,
    source_creative_id: str,
    target_creative_id: str,
    *,
    auto: bool,
    force_reason: str = "",
    commit: bool = True,
) -> list[GuardHit]:
    """把 source 合并进 target（source 删除），返回全部守卫命中（含 warn）。

    校验失败/守卫拦截抛 ApiError（人工）或 MergeBlocked（自动）。
    commit=False 时不落 commit——自动合并跑在调用方的事务边界里
    （judge_pipeline 的 begin_nested 容错依赖它），由调用方统一提交。
    """
    if source_creative_id == target_creative_id:
        raise ApiError(400, "不能与自身合并（source 与 target 相同）")
    creative_repo = CreativeRepository(db)
    source = creative_repo.get(source_creative_id)
    if source is None:
        raise ApiError(404, f"Creative 不存在：{source_creative_id}")
    target = creative_repo.get(target_creative_id)
    if target is None:
        raise ApiError(404, f"Creative 不存在：{target_creative_id}")

    # Merge guard（案例 13 教训）：既定"维持拆分"裁决被推翻时必须带理由。
    hits = check_merge(source, target, db)
    blocks = [hit for hit in hits if hit.level == "block"]
    if blocks:
        if auto:
            # 自动合并无 force 概念：任何 block 都直接不执行
            raise MergeBlocked("；".join(hit.message for hit in blocks))
        if not force_reason.strip():
            raise ApiError(
                400,
                "合并被守卫拦截：" + "；".join(hit.message for hit in blocks),
            )

    variant_repo = VariantRepository(db)
    for variant in variant_repo.list_by_creative(source.id):
        variant_repo.move_to_creative(variant, target.id)
    creative_repo.delete(source)
    # Human graph curation is Similarity-agent learning material (§5).
    forced_note = f" [forced: {force_reason}]" if blocks else ""
    new_value = f"{target.name} ({target.id}){forced_note}"
    if auto:
        new_value = f"auto: {new_value}"
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=target.id,
        action="merge",
        old_value=f"{source.name} ({source.id})",
        new_value=new_value,
    )
    if commit:
        db.commit()

    try:
        graph_sync.get_graph_repository(settings).merge_creatives(
            source.id, target.id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j merge 同步失败: %s", exc)
    rebuild_mirror(db)

    # 传递闭包：合并后 target 的特征变了（变体并入），原本相似度不够的
    # 漏网对可能因此浮出——对 target 局部重扫（O(N)，失败静默）。
    # 懒 import：missed_merge_scan 也调 merge_creatives，顶层会循环依赖。
    try:
        from app.services.missed_merge_scan import scan_for_creative

        scan_for_creative(db, settings, target.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("合并后局部重扫失败 target=%s: %s", target.id, exc)
    return hits
