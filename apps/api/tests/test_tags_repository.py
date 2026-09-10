"""TagRepository 并发安全回归测试。

背景：批量上传 = 每文件一个后台分析线程，多个 pipeline 同时
set_asset_tags 同名 tag 时，旧版 get_or_create 的 check-then-insert
会撞 tags_name_key 唯一约束（UniqueViolation）导致分析失败。

这里需要真实提交（db_session fixture 的回滚事务模拟不了并发），
所以直接用 test_db_url 建 engine，测完手动清理。
"""

from __future__ import annotations

import threading
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import CreativeAsset, Tag
from app.repositories.tags import TagRepository


def _make_asset(asset_id: str) -> CreativeAsset:
    return CreativeAsset(
        id=asset_id,
        filename=f"{asset_id}.mp4",
        file_type="video",
        mime_type="video/mp4",
        storage_key=f"test-race-{asset_id}",
        size_bytes=1,
        analysis_status="completed",
    )


def test_set_asset_tags_concurrent_same_tag_name(test_db_url: str) -> None:
    engine = create_engine(test_db_url)
    tag_name = f"race-{uuid.uuid4().hex[:12]}"
    asset_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(asset_id: str) -> None:
        try:
            with Session(bind=engine) as session:
                session.add(_make_asset(asset_id))
                session.flush()
                barrier.wait(timeout=30)  # 对齐两个线程的建 tag 时机
                TagRepository(session).set_asset_tags(asset_id, [tag_name])
                session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(aid,)) for aid in asset_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    try:
        assert not errors, f"并发建同名 tag 抛错: {errors!r}"
        with Session(bind=engine) as session:
            tags = session.scalars(select(Tag).where(Tag.name == tag_name)).all()
            assert len(tags) == 1
    finally:
        with Session(bind=engine) as session:
            for asset_id in asset_ids:
                asset = session.get(CreativeAsset, asset_id)
                if asset is not None:
                    session.delete(asset)  # tag_assignments 级联删除
            session.flush()
            tag = session.scalar(select(Tag).where(Tag.name == tag_name))
            if tag is not None:
                session.delete(tag)
            session.commit()
        engine.dispose()
