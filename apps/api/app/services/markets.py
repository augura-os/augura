"""Market-prefix configuration — 市场前缀与相似度停用词的可配置化。

文件名里的市场标签（如 KS_EN / KS_KR）是**项目级**信息，不是产品级：
默认值保持中性，具体项目的前缀通过 settings 键 `market_prefixes`
（逗号分隔）配置；相似度计算的通用停用词同理
（`similarity_generic_tokens`）。

这是公私分离的一部分：私有部署把自己的前缀写进 settings 即可，
代码里不出现任何具体项目标识。
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.repositories.settings import SettingsRepository

MARKET_PREFIXES_SETTING = "market_prefixes"
GENERIC_TOKENS_SETTING = "similarity_generic_tokens"
MARKET_TAG_MAP_SETTING = "market_tag_map"
MARKET_ALIASES_SETTING = "market_aliases"

# 中性默认（示例值；真实项目在 Settings 里覆盖）
DEFAULT_MARKET_PREFIXES = ("KS_EN", "KS_KR")

# 规范市场码表（码 → 展示名）：市场 = 语言区，不是项目（ABCBR 是项目 ABC
# + 市场 PT 的混合前缀，系统内统一归一到 PT）。表只为识别和展示；
# settings 键 market_aliases 可追加同一市场的别名写法。
MARKET_CODES: dict[str, str] = {
    "US": "美国（英语区）",
    "PT": "葡语（巴西）",
    "ES": "西语（拉美）",
    "KR": "韩国",
    "JP": "日本",
    "TW": "繁中（中国台湾）",
    "DE": "德国",
    "FR": "法国",
    "VN": "越南",
    "TH": "泰国",
    "ID": "印尼",
    "RU": "俄罗斯",
    "TR": "土耳其",
    "HI": "印度（印地语）",
}

# 市场码别名（同一市场的不同写法归一；settings 键 market_aliases 可追加，
# JSON）。EN/US 同市场：英语区暂时按美国地区。
MARKET_ALIASES: dict[str, str] = {"EN": "US"}

# 分析标签 → 市场前缀的默认归一映射（键小写；值必须是已配置的市场前缀，
# 否则整条丢弃——防止映射表把标签指到不存在的词表外市场）。
# 默认值必须为空：真实项目的映射是私有信息，进 settings 键
# `market_tag_map`（JSON），公开代码零项目痕迹。
DEFAULT_MARKET_TAG_MAP: dict[str, str] = {}

# 品类通用词：它们让名称相似度虚高（两条素材仅因共享 "-village-builder"
# 就压线 0.20），计算前过滤。可按项目用 settings 追加。
DEFAULT_GENERIC_TOKENS = {
    "village", "city", "builder", "building", "town", "island", "ad",
    "game", "sim",
}


def resolve_market_prefixes(db: Session | None = None) -> tuple[str, ...]:
    if db is not None:
        raw = SettingsRepository(db).get(MARKET_PREFIXES_SETTING)
        if raw:
            prefixes = tuple(p.strip() for p in raw.split(",") if p.strip())
            if prefixes:
                return prefixes
    return DEFAULT_MARKET_PREFIXES


def resolve_generic_tokens(db: Session | None = None) -> set[str]:
    tokens = set(DEFAULT_GENERIC_TOKENS)
    if db is not None:
        raw = SettingsRepository(db).get(GENERIC_TOKENS_SETTING)
        if raw:
            tokens |= {t.strip().lower() for t in raw.split(",") if t.strip()}
    return tokens


def _prefix_re(prefixes: tuple[str, ...]) -> re.Pattern[str]:
    escaped = "|".join(re.escape(prefix) for prefix in prefixes)
    return re.compile(rf"^(?:{escaped})[-_]?", re.IGNORECASE)


def market_key(filename: str, prefixes: tuple[str, ...]) -> str:
    """Filename identity ignoring the market prefix (KS_EN vs KS_KR)."""
    from app.services.matching import normalize

    return _prefix_re(prefixes).sub("", normalize(filename), count=1)


def market_tag(
    filename: str,
    prefixes: tuple[str, ...],
    aliases: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Split (market_code, rest): ("KS_EN-foo-竖.mp4") -> ("US", "foo-竖").

    市场标签 = 规范市场码（prefix_to_market_code：ABCBR → PT，
    KS_EN → EN → 别名 US）；未命中码表的前缀整段原样。无匹配时返回
    ("", filename)。
    """
    stem = filename.rsplit(".", 1)[0]
    for prefix in prefixes:
        if stem.upper().startswith(prefix.upper() + "-"):
            return prefix_to_market_code(prefix, aliases), stem[len(prefix) + 1 :]
    return "", stem


def has_market_prefix(filename: str, prefixes: tuple[str, ...]) -> bool:
    """文件名带市场前缀 → language-market 换皮因子。"""
    stem = filename.rsplit(".", 1)[0]
    return any(stem.upper().startswith(prefix.upper()) for prefix in prefixes)


def resolve_market_aliases(db: Session | None = None) -> dict[str, str]:
    """市场码别名表：内置 MARKET_ALIASES + settings 键 market_aliases（JSON）追加。"""
    aliases = dict(MARKET_ALIASES)
    if db is not None:
        raw = SettingsRepository(db).get(MARKET_ALIASES_SETTING)
        if raw:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                aliases.update(
                    {
                        str(k).strip().upper(): str(v).strip().upper()
                        for k, v in payload.items()
                        if isinstance(k, str) and isinstance(v, str)
                    }
                )
    return aliases


def prefix_to_market_code(
    prefix: str, aliases: dict[str, str] | None = None
) -> str:
    """市场前缀 → 规范市场码。

    - 前缀的**最长后缀**命中已知码（MARKET_CODES）或别名 → 取码
      （ABCBR→PT、ABCES→ES、KS_EN→EN）
    - 命中后过一遍别名表归一（EN→US；最多一跳，不做递归防环）
    - 未命中 → 整段原样（向后兼容奇特约定）
    """
    aliases = MARKET_ALIASES if aliases is None else aliases
    raw = prefix.strip().upper()
    if not raw:
        return raw
    for size in range(len(raw), 1, -1):
        suffix = raw[-size:]
        if suffix in MARKET_CODES or suffix in aliases:
            return aliases.get(suffix, suffix)
    return raw


def prefix_tag(prefix: str, aliases: dict[str, str] | None = None) -> str:
    """市场前缀 → 规范市场码（prefix_to_market_code 的别名）。"""
    return prefix_to_market_code(prefix, aliases)


def resolve_market_tag_map(db: Session | None = None) -> dict[str, str]:
    """分析标签 → 市场标识的归一映射（settings 键 market_tag_map，JSON）。

    值可以是已配置的市场前缀（ABCBR）或直接是市场码（PT，
    migrate_market_codes 迁移后的写法）——两者之外的一律丢弃；
    resolve_market 的 observed 路径统一过 prefix_tag 归一成码。
    DB 行缺失/非法时回落 DEFAULT_MARKET_TAG_MAP。
    """
    payload: object = DEFAULT_MARKET_TAG_MAP
    if db is not None:
        raw = SettingsRepository(db).get(MARKET_TAG_MAP_SETTING)
        if raw:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = {}
    if not isinstance(payload, dict):
        return {}
    valid_values = {p.upper() for p in resolve_market_prefixes(db)}
    valid_values |= set(MARKET_CODES) | set(resolve_market_aliases(db))
    return {
        str(tag).strip().lower(): str(prefix).strip()
        for tag, prefix in payload.items()
        if isinstance(tag, str)
        and isinstance(prefix, str)
        and prefix.strip().upper() in valid_values
    }


def resolve_market(
    filename: str,
    analysis_tags: list[str] | None,
    prefixes: tuple[str, ...],
    tag_map: dict[str, str],
    aliases: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """双层市场判定：declared（文件名前缀）× observed（AI 分析标签映射）。

    文件名前缀是命名约定不是事实，分析标签（配音语言揭示的目标市场）
    是内容证据——两层交叉验证。返回 (市场码 | None, 置信度)：

    - 两者一致 → (code, "high")
    - 不一致 → (declared, "conflict")——存疑，基准桶不收（宁可缺数据
      不污染基准），由收件箱 market_conflict 条目提示人工核对
    - 只有 declared（分析没出市场标签）→ (declared, "medium")
    - 都没有 → (None, "none")

    市场码与 market_tag/market_stats 同一命名空间（prefix_to_market_code
    归一：ABCBR→PT、KS_EN→US）。
    """
    declared, _rest = market_tag(filename, prefixes, aliases)
    declared_tag = declared or None
    observed: str | None = None
    for tag in analysis_tags or []:
        mapped = tag_map.get(str(tag).strip().lower())
        if mapped:
            observed = prefix_tag(mapped, aliases)
            break
    if declared_tag and observed:
        if declared_tag == observed:
            return declared_tag, "high"
        return declared_tag, "conflict"
    if declared_tag:
        return declared_tag, "medium"
    return None, "none"


def resolve_creative_markets(db: Session) -> dict[str, tuple[str, str]]:
    """全量 creative 的市场判定：creative_id → (市场标签, 置信度)。

    逐变体跑 resolve_market（变体资产文件名 + 其 AI 分析 tags）；
    任一变体冲突 → 整个 creative 存疑（conflict）；否则取第一个判定出
    市场的变体。遥测分桶与收件箱市场存疑条目共用这一处口径。
    """
    from sqlalchemy import select

    from app.models import AnalysisResult, CreativeAsset, CreativeVariant

    prefixes = resolve_market_prefixes(db)
    tag_map = resolve_market_tag_map(db)
    aliases = resolve_market_aliases(db)
    rows = db.execute(
        select(CreativeVariant.creative_id, CreativeAsset.filename, AnalysisResult.tags)
        .join(CreativeAsset, CreativeAsset.id == CreativeVariant.asset_id)
        .outerjoin(AnalysisResult, AnalysisResult.asset_id == CreativeAsset.id)
        .order_by(CreativeVariant.created_at, CreativeVariant.id)
    ).all()
    result: dict[str, tuple[str, str]] = {}
    for creative_id, filename, tags in rows:
        market, confidence = resolve_market(
            filename, tags or [], prefixes, tag_map, aliases
        )
        existing = result.get(creative_id)
        if confidence == "conflict" or (existing is not None and existing[1] == "conflict"):
            result[creative_id] = (market or (existing[0] if existing else ""), "conflict")
        elif existing is None:
            result[creative_id] = (market or "", confidence)
        elif not existing[0] and market:
            result[creative_id] = (market, confidence)
    return result
