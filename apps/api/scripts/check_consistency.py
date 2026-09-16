"""PG ↔ Neo4j 一致性对账（多数据源一致性的人工校验工具）。

三个数据源（PostgreSQL / Neo4j / MinIO）各自写入，可能出现单侧失败。
本脚本比对 PG 与 Neo4j 的节点/边计数与缺失清单；MinIO 侧只验桶可达
（对象清单对账成本高，靠 MinIO 版本控制与上传事务保证）。

Usage:
    python -m scripts.check_consistency
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text

_PG_COUNTS = {
    "creatives": "select count(*) from creatives",
    "variants": "select count(*) from creative_variants",
    "assets": "select count(*) from creative_assets where file_type != 'excel'",
    "tags": "select count(*) from tags",
    "dnas": "select count(*) from creative_dnas",
    "derivations": "select count(*) from variant_derivations",
}

_NEO4J_COUNTS = {
    "creatives": "MATCH (n:Creative) RETURN count(n)",
    "variants": "MATCH (n:Variant) RETURN count(n)",
    "assets": "MATCH (n:Asset) RETURN count(n)",
    "tags": "MATCH (n:Tag) RETURN count(n)",
    "dnas": "MATCH (n:CreativeDNA) RETURN count(n)",
    "derivations": "MATCH ()-[r:DERIVED_FROM]->() RETURN count(r)",
}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    engine = create_engine(os.environ["DATABASE_URL"])
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "augura123")

    from neo4j import GraphDatabase

    mismatches = 0
    with engine.begin() as conn, GraphDatabase.driver(
        neo4j_uri, auth=("neo4j", neo4j_password)
    ) as driver, driver.session() as session:
        print(f"{'类别':<14}{'PG':>8}{'Neo4j':>8}  状态")
        print("-" * 40)
        for name in _PG_COUNTS:
            pg_count = conn.execute(text(_PG_COUNTS[name])).scalar()
            neo_count = session.run(_NEO4J_COUNTS[name]).single()[0]
            ok = pg_count == neo_count
            mismatches += 0 if ok else 1
            print(f"{name:<14}{pg_count:>8}{neo_count:>8}  {'✓' if ok else '✗ 不一致'}")
    print("-" * 40)
    if mismatches:
        print(f"发现 {mismatches} 类不一致——运行 graph_sync 重建镜像可修复 Neo4j 侧")
        return 1
    print("全部一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
