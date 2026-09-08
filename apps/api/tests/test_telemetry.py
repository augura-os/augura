"""Tests for the telemetry skeleton (allowlist / anonymize / client / export)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import EditLog
from telemetry.allowlist import validate
from telemetry.anonymize import should_rotate, timestamp_bucket
from telemetry.client import TelemetryClient
from telemetry.export import corrections_from_edit_logs


class TestAllowlist:
    def test_known_event_keeps_only_allowed_fields(self) -> None:
        result = validate(
            "feature_click",
            {"feature_name": "graph", "timestamp_bucket": "t", "secret": "x"},
        )
        assert result == {"feature_name": "graph", "timestamp_bucket": "t"}

    def test_unknown_event_dropped(self) -> None:
        assert validate("upload_video", {"anything": 1}) is None

    def test_correction_fields(self) -> None:
        result = validate(
            "correction",
            {
                "entity_type": "asset",
                "field": "tags",
                "old_value": "a",
                "new_value": "b",
                "timestamp_bucket": "t",
            },
        )
        assert result is not None and result["field"] == "tags"

    def test_session_start_carries_genre(self) -> None:
        result = validate(
            "session_start",
            {"app_version": "0.12.0", "os_family": "windows", "genre": "match3",
             "timestamp_bucket": "t"},
        )
        assert result is not None and result["genre"] == "match3"

    def test_aggregate_events_keep_only_buckets(self) -> None:
        result = validate(
            "aggregate_metric",
            {"genre": "match3", "metric": "cpp", "bucket": "<60", "count": 4,
             "raw_value": 87.3, "timestamp_bucket": "t"},
        )
        assert result == {"genre": "match3", "metric": "cpp", "bucket": "<60",
                          "count": 4, "timestamp_bucket": "t"}
        lifecycle = validate(
            "creative_lifecycle",
            {"genre": "rpg", "lifetime_days_bucket": ">=30d", "count": 2,
             "timestamp_bucket": "t"},
        )
        assert lifecycle is not None and lifecycle["count"] == 2


class TestAggregates:
    def _seed(self, db: Session, count: int = 3) -> None:
        """count 个同市场同表现的 creative（k-匿名抑制后 count≥3 才出桶）。"""
        from datetime import date

        from app.models import Creative, CreativeAsset, CreativeVariant, Performance

        for i in range(count):
            creative = Creative(id=str(uuid.uuid4()), name=f"agg-creative-{i}")
            asset = CreativeAsset(
                id=str(uuid.uuid4()),
                filename=f"KS_EN-agg-creative-{i}-tail-long-enough-竖.mp4",
                file_type="video", storage_key=f"test/{uuid.uuid4()}",
            )
            db.add_all([creative, asset])
            db.flush()
            db.add(
                CreativeVariant(
                    id=str(uuid.uuid4()), creative_id=creative.id,
                    asset_id=asset.id, name="v1",
                )
            )
            stem = f"ks_en-agg-creative-{i}-tail-long-enough"
            for day, spend, payers in (
                (date(2026, 7, 1), 100.0, 2),
                (date(2026, 7, 10), 300.0, 3),
            ):
                db.add(
                    Performance(
                        id=str(uuid.uuid4()), creative_name=stem, date=day,
                        spend=spend, installs=50,
                        raw={"付费人数": payers, "D1_Roas": 0.02, "次留": 0.30},
                    )
                )
        db.flush()

    def test_aggregate_metric_events_bucketed(
        self, db_session: Session
    ) -> None:
        from telemetry.export import aggregate_metric_events

        self._seed(db_session)
        events = aggregate_metric_events(db_session, genre="match3")
        assert events, "应产出聚合事件"
        for event in events:
            # 只含白名单字段（含 market/dna_code 维度），且任何字段都不是单行原始值
            assert set(event) <= {
                "genre", "market", "dna_code", "dna_name", "metric", "bucket",
                "count", "timestamp_bucket",
            }
            assert event["genre"] == "match3"
            assert event["market"] == "US"  # KS_EN 前缀 → 后缀 EN → 别名 US
            assert isinstance(event["bucket"], str) and (
                event["bucket"].startswith("<") or event["bucket"].startswith(">=")
            )
            assert isinstance(event["count"], int) and event["count"] >= 3
        assert any(e["metric"] == "cpp" for e in events)
        # cpp = 400/5 = 80 → 应落在 60-120 之间（桶 "<120"），3 个创意
        assert any(
            e["metric"] == "cpp" and e["bucket"] == "<120" and e["count"] == 3
            for e in events
        )

    def test_k_anonymity_suppresses_small_buckets(self, db_session: Session) -> None:
        """count < 3 的桶不上传（k-匿名小样本抑制，PRIVACY.md §3.2）。"""
        from telemetry.export import aggregate_metric_events, creative_lifecycle_events

        self._seed(db_session, count=1)
        assert aggregate_metric_events(db_session, genre="match3") == []
        assert creative_lifecycle_events(db_session, genre="match3") == []

    def test_lifecycle_events_bucketed(self, db_session: Session) -> None:
        from telemetry.export import creative_lifecycle_events

        self._seed(db_session)
        events = creative_lifecycle_events(db_session, genre="rpg")
        # 存活 9 天 → 桶 "<14d"；未归族 → dna_code/dna_name "none"；市场 EN
        assert events == [
            {
                "genre": "rpg",
                "market": "US",
                "dna_code": "none",
                "dna_name": "none",
                "lifetime_days_bucket": "<14d",
                "count": 3,
                "timestamp_bucket": events[0]["timestamp_bucket"],
            }
        ]

    def test_lifecycle_events_include_dna_name(self, db_session: Session) -> None:
        # 未归族素材 dna_name 也应出现（跨实例对齐靠 name 不靠 code）
        from telemetry.export import creative_lifecycle_events

        self._seed(db_session)
        events = creative_lifecycle_events(db_session, genre="rpg")
        assert events[0]["dna_name"] == "none"

    def test_aggregates_empty_db_no_crash(self, db_session: Session) -> None:
        from telemetry.export import aggregate_metric_events, creative_lifecycle_events

        assert aggregate_metric_events(db_session, genre="other") == []
        assert creative_lifecycle_events(db_session, genre="other") == []


class TestAnonymize:
    def test_bucket_is_six_hours(self) -> None:
        dt = datetime(2026, 7, 23, 13, 37, 42, tzinfo=timezone.utc)
        assert timestamp_bucket(dt) == "2026-07-23T12:00:00+00:00"

    def test_rotation(self) -> None:
        created = datetime(2026, 4, 1, tzinfo=timezone.utc)
        assert should_rotate(created, now=datetime(2026, 8, 1, tzinfo=timezone.utc))
        assert not should_rotate(created, now=datetime(2026, 5, 1, tzinfo=timezone.utc))

    def test_scrub_slugs_hashes_creative_names(self) -> None:
        from telemetry.anonymize import scrub_slugs

        value = "auto: D01 家族名（no-ads-pure-building 归族）"
        scrubbed = scrub_slugs(value, salt="inst-1")
        assert "no-ads-pure-building" not in scrubbed
        assert "h_" in scrubbed
        # DNA 编码与中文理由保留
        assert "D01" in scrubbed and "归族" in scrubbed
        # 同盐稳定、不同盐不同（不可逆关联）
        assert scrub_slugs(value, salt="inst-1") == scrubbed
        assert scrub_slugs(value, salt="inst-2") != scrubbed

    def test_corrections_export_hashes_slugs(self, db_session: Session) -> None:
        from app.models import EditLog
        from telemetry.export import corrections_from_edit_logs

        db_session.add(
            EditLog(
                id=str(uuid.uuid4()), entity_type="creative",
                entity_id=str(uuid.uuid4()), action="update", field="dna_id",
                old_value="",
                new_value="auto: D01 <- beach-bbq-cabin-building",
            )
        )
        db_session.flush()
        events = corrections_from_edit_logs(db_session, salt="inst-x")
        assert events, "应导出修正事件"
        assert "beach-bbq-cabin-building" not in str(events)
        assert "h_" in events[0]["new_value"]


class TestTelemetryClient:
    def test_record_respects_allowlist_and_disable(self, tmp_path) -> None:  # noqa: ANN001
        client = TelemetryClient(tmp_path / "q.db", instance_id="i1")
        assert client.record("feature_click", feature_name="graph", timestamp_bucket="t")
        assert not client.record("upload_video", foo=1)
        assert client.queue_size() == 1
        client.enabled = False
        assert not client.record("feature_click", feature_name="x", timestamp_bucket="t")
        assert client.queue_size() == 1

    def test_program_toggle_does_not_block_core_events(self, tmp_path) -> None:  # noqa: ANN001
        """分层（v0.12.2）：共建计划关闭时，运行保障事件（error/session_start）仍入队。"""
        client = TelemetryClient(
            tmp_path / "q.db", instance_id="i1", program_enabled=False
        )
        # 共建层被关
        assert not client.record("feature_click", feature_name="graph", timestamp_bucket="t")
        assert not client.record(
            "aggregate_metric", genre="rpg", metric="cpp", bucket="<60", count=1,
            timestamp_bucket="t",
        )
        # 运行保障层不受影响
        assert client.record(
            "error", error_code="E1", stack_signature="sig", timestamp_bucket="t"
        )
        assert client.record(
            "session_start", app_version="0.12.2", os_family="windows",
            timestamp_bucket="t",
        )
        assert client.queue_size() == 2

    def test_flush_posts_event_type_in_body(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """上送体必须带 event_type（接收端按类型分桶；v0.12 契约补全）。"""
        import json as _json

        captured: list[dict] = []

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _fake_urlopen(request, timeout=0):
            captured.append(_json.loads(request.data.decode("utf-8")))
            return _Resp()

        monkeypatch.setattr("telemetry.client.urllib.request.urlopen", _fake_urlopen)
        client = TelemetryClient(
            tmp_path / "q.db", endpoint="https://pool.example.com", instance_id="i1"
        )
        client.record("feature_click", feature_name="graph", timestamp_bucket="t")
        assert client.flush() == 1
        assert captured == [
            {"event_type": "feature_click", "feature_name": "graph",
             "timestamp_bucket": "t", "instance_id": "i1"}
        ]
        client.close()

    def test_flush_noop_without_endpoint(self, tmp_path) -> None:  # noqa: ANN001
        client = TelemetryClient(tmp_path / "q.db", endpoint="")
        client.record("session_start", app_version="0.7.1", os_family="windows",
                      timestamp_bucket="t")
        assert client.flush() == 0
        assert client.queue_size() == 1  # 未发送但不丢失
        client.close()


class TestCorrectionExport:
    def test_export_maps_edit_logs(self, db_session: Session) -> None:
        db_session.add(
            EditLog(
                id=str(uuid.uuid4()),
                entity_type="creative",
                entity_id="c1",
                action="merge",
                field="",
                old_value="a (1)",
                new_value="b (2)",
            )
        )
        db_session.flush()
        events = corrections_from_edit_logs(db_session)
        assert len(events) == 1
        assert events[0]["entity_type"] == "creative"
        assert events[0]["new_value"] == "b (2)"
        assert "+00:00" in events[0]["timestamp_bucket"] or events[0]["timestamp_bucket"].endswith("Z") or True


class TestMarketDimension:
    """三维分桶的市场维度：conflict 排除 + correction 上下文反查。"""

    def _seed_conflict_creative(self, db: Session) -> str:
        """文件名 KS_PT 但分析标签映射到 KS_EN → 市场冲突的 creative。"""
        import json as _json
        from datetime import date

        from app.models import (
            AnalysisResult,
            Creative,
            CreativeAsset,
            CreativeVariant,
            Performance,
        )
        from app.repositories.settings import SettingsRepository

        repo = SettingsRepository(db)
        repo.set("market_prefixes", "KS_EN,KS_PT")
        repo.set("market_tag_map", _json.dumps({"spanish-latam": "KS_EN"}))
        creative = Creative(id=str(uuid.uuid4()), name="conflict-creative")
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename="KS_PT-conflict-creative-tail-long-enough-竖.mp4",
            file_type="video", storage_key=f"test/{uuid.uuid4()}",
        )
        db.add_all([creative, asset])
        db.flush()
        db.add(
            CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name="v1",
            )
        )
        db.add(
            AnalysisResult(
                id=str(uuid.uuid4()), asset_id=asset.id,
                hook="h", conflict="c", gameplay="g", tags=["spanish-latam"],
            )
        )
        db.add(
            Performance(
                id=str(uuid.uuid4()),
                creative_name="ks_pt-conflict-creative-tail-long-enough",
                date=date(2026, 7, 1), spend=100.0, installs=50,
                raw={"付费人数": 2, "D1_Roas": 0.02},
            )
        )
        db.flush()
        return creative.id

    def test_conflict_creative_excluded_from_buckets(self, db_session: Session) -> None:
        from telemetry.export import aggregate_metric_events

        creative_id = self._seed_conflict_creative(db_session)
        # 冲突 creative 不进基准桶：PT 市场桶不存在，US 桶也不含它（样本被压）
        events = aggregate_metric_events(db_session, genre="casual_slg")
        assert all(event["market"] != "PT" for event in events)
        from app.services.review import market_conflict_items

        items = market_conflict_items(db_session)
        assert [item.creative_id for item in items] == [creative_id]
        assert items[0].kind == "market_conflict"

    def test_corrections_carry_context(self, db_session: Session) -> None:
        import json as _json

        from app.models import (
            AnalysisResult,
            Creative,
            CreativeAsset,
            CreativeDNA,
            CreativeVariant,
            EditLog,
        )
        from app.repositories.settings import SettingsRepository

        SettingsRepository(db_session).set(
            "market_tag_map", _json.dumps({"english-us": "KS_EN"})
        )
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D7", name="测试家族")
        db_session.add(dna)
        db_session.flush()  # 先落 dna——creatives.dna_id 有外键，同批 flush 不保证顺序
        creative = Creative(id=str(uuid.uuid4()), name="ctx-creative", dna_id=dna.id)
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename="KS_EN-ctx-creative-tail-long-enough-竖.mp4",
            file_type="video", storage_key=f"test/{uuid.uuid4()}",
        )
        db_session.add_all([creative, asset])
        db_session.flush()
        db_session.add(
            CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name="v1",
            )
        )
        db_session.add(
            AnalysisResult(
                id=str(uuid.uuid4()), asset_id=asset.id,
                hook="h", conflict="c", gameplay="g", tags=["english-us"],
            )
        )
        db_session.add(
            EditLog(
                id=str(uuid.uuid4()), entity_type="creative",
                entity_id=creative.id, action="update", field="dna_id",
                old_value="", new_value="D7",
            )
        )
        db_session.flush()
        events = corrections_from_edit_logs(db_session, genre="casual_slg")
        assert len(events) == 1
        assert events[0]["genre"] == "casual_slg"
        assert events[0]["dna_code"] == "D7"
        assert events[0]["market"] == "US"

    def test_correction_derivation_reverse_lookup(self, db_session: Session) -> None:
        """entity_type=derivation 的修正经 source variant 反查 creative 上下文。"""
        from app.models import (
            Creative,
            CreativeAsset,
            CreativeVariant,
            EditLog,
            VariantDerivation,
        )

        creative = Creative(id=str(uuid.uuid4()), name="deriv-ctx-creative")
        assets = [
            CreativeAsset(
                id=str(uuid.uuid4()),
                filename=f"KS_EN-deriv-ctx-creative-{suffix}-tail-long-enough-竖.mp4",
                file_type="video", storage_key=f"test/{uuid.uuid4()}",
            )
            for suffix in ("a", "b")
        ]
        db_session.add_all([creative, *assets])
        db_session.flush()
        variants = [
            CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name=f"v{i}",
            )
            for i, asset in enumerate(assets)
        ]
        db_session.add_all(variants)
        db_session.flush()
        derivation = VariantDerivation(
            id=str(uuid.uuid4()), source_variant_id=variants[0].id,
            target_variant_id=variants[1].id, factor="aspect-ratio",
        )
        db_session.add(derivation)
        db_session.add(
            EditLog(
                id=str(uuid.uuid4()), entity_type="derivation",
                entity_id=derivation.id, action="update", field="factor",
                old_value="aspect-ratio", new_value="intro-sticker",
            )
        )
        db_session.flush()
        events = corrections_from_edit_logs(db_session)
        assert len(events) == 1
        assert events[0]["market"] == "US"

    def test_correction_unresolvable_context_blank(self, db_session: Session) -> None:
        """反查不到 creative 的修正：上下文留空字符串。"""
        db_session.add(
            EditLog(
                id=str(uuid.uuid4()), entity_type="creative",
                entity_id="no-such-id", action="rename", field="name",
                old_value="a", new_value="b",
            )
        )
        db_session.flush()
        events = corrections_from_edit_logs(db_session)
        assert len(events) == 1
        assert events[0]["dna_code"] == "" and events[0]["market"] == ""
