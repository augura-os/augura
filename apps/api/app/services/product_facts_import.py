"""product-facts 文件导入：上传产品资料 → 文本抽取 → LLM 抽取产品事实。

不落库（human-in-the-loop）：只返回 ``{product_facts, extracted_chars,
truncated}``，由前端回填表单、用户检查后走正常 PUT /settings 保存。

- 白名单扩展名 .md/.txt/.docx/.pdf，单文件上限 5 MB（文件类错误照
  ingest 惯例直接抛 ApiError 400）。
- 文本抽取：md/txt 按 utf-8 解码（errors=replace）；docx 用标准库
  zipfile + xml.etree 解 word/document.xml（w:t 拼接、w:p 分段换行）；
  pdf 用 pypdf 逐页 extract_text。抽取文本截断到 MAX_EXTRACT_CHARS。
- LLM 抽取走 llm_judge.complete_json（max_tokens 2000）；输出形状对齐
  PUT product_facts 的已知键+类型纪律，非法重试一次，仍非法 502 不写任何东西。
- AI key 缺失照 lab 的 ai_key_not_configured 人话模式（409）。
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import ApiError
from app.services.llm_judge import complete_json
from app.services.settings import (
    PRODUCT_FACTS_ALLOWED_KEYS,
    PRODUCT_FACTS_LIST_KEYS,
    PRODUCT_FACTS_STR_KEY,
    resolve_ai_config,
)

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (".md", ".txt", ".docx", ".pdf")
MAX_FILE_BYTES = 5 * 1024 * 1024
# 抽取文本进 prompt 前的截断上限：~20000 字符足够装一份产品说明书
MAX_EXTRACT_CHARS = 20000
IMPORT_MAX_TOKENS = 2000

# docx 主文档的 WordprocessingML 命名空间
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

IMPORT_SYSTEM = """你是产品资料整理助手。从用户提供的产品资料文本中提取「产品事实」，
供后续广告素材诊断与评审使用。

只输出一个 JSON 对象，固定六个键：
- "core_loop"：一句话核心循环（玩家每分钟在做什么）；资料未提及给 ""
- "selling_points"：卖点清单（字符串数组）；资料未提及给 []
- "must_show"：广告中必须展示的元素（字符串数组）；资料未提及给 []
- "forbidden_claims"：禁止表达的承诺/说法（字符串数组）；资料未提及给 []
- "compliance_limits"：合规限制（字符串数组）；资料未提及给 []
- "success_metric_defs"：成功指标口径定义（字符串数组）；资料未提及给 []

铁律：
1. 只提取资料中明确写到的信息；资料没有的键给空值，严禁编造、严禁脑补。
2. 除 JSON 键名外，所有输出内容必须是简体中文（资料为其它语言时翻译为中文）。
3. 每个数组条目是一句自包含的话，不引用「上文」「文档」等上下文。
"""


@dataclass
class ProductFactsImportOutcome:
    """导入解析结果（路由层据此组装响应；不落库）。"""

    product_facts: dict[str, Any]
    # 抽取文本字符数（截断前），供前端/排障判断资料规模
    extracted_chars: int
    # 抽取文本超过 MAX_EXTRACT_CHARS 被截断
    truncated: bool


class TextExtractionError(RuntimeError):
    """文件损坏或解析失败，无法抽取纯文本（调用方映 400 unreadable）。"""


def _extract_docx(data: bytes) -> str:
    """标准库解 docx：word/document.xml 的 w:t 拼接、w:p 分段换行。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml")
    root = ElementTree.fromstring(document)
    paragraphs = [
        "".join(node.text or "" for node in paragraph.iter(f"{_W_NS}t"))
        for paragraph in root.iter(f"{_W_NS}p")
    ]
    return "\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_text(extension: str, data: bytes) -> str:
    """按扩展名抽取纯文本；文件损坏/解析失败抛 TextExtractionError。"""
    if extension in (".md", ".txt"):
        return data.decode("utf-8", errors="replace")
    try:
        if extension == ".docx":
            return _extract_docx(data)
        return _extract_pdf(data)
    except Exception as exc:  # noqa: BLE001
        logger.info("product-facts 导入文件不可读（%s）：%s", extension, exc)
        raise TextExtractionError(str(exc)) from None


def validate_facts(payload: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """LLM 输出形状校验：对齐 PUT product_facts 的已知键+类型纪律。

    合法返回规整后的六键 dict（缺省键补空值）；非法（非 dict / 未知键 /
    已知键类型错）返回 None，由调用方决定是否重试。
    """
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if key not in PRODUCT_FACTS_ALLOWED_KEYS:
            return None
        if value is None:
            continue  # 模型给 null 视为空值，规整阶段补默认
        if key == PRODUCT_FACTS_STR_KEY and not isinstance(value, str):
            return None
        if key in PRODUCT_FACTS_LIST_KEYS and (
            not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
        ):
            return None
    return {
        PRODUCT_FACTS_STR_KEY: str(payload.get(PRODUCT_FACTS_STR_KEY) or ""),
        **{
            key: [str(item) for item in payload.get(key) or []]
            for key in PRODUCT_FACTS_LIST_KEYS
        },
    }


def import_product_facts(
    db: Session, settings: Settings, upload: UploadFile
) -> ProductFactsImportOutcome:
    """上传文件 → 抽取文本 → LLM 抽取产品事实；不落库。"""
    filename = upload.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ApiError(
            400,
            f"不支持的文件类型：{filename}（支持 md/txt/docx/pdf）",
            code="product_facts_import_unsupported_type",
            params={"filename": filename},
        )
    data = upload.file.read()
    if not data:
        raise ApiError(
            400,
            f"文件为空：{filename}",
            code="product_facts_import_empty_file",
            params={"filename": filename},
        )
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(
            400,
            f"文件超过大小上限：{filename}（上限 {MAX_FILE_BYTES / 1024 ** 2:g} MB）",
            code="product_facts_import_too_large",
            # 原始字节数交由前端按界面语言格式化（GB/MB）
            params={"filename": filename, "limit_bytes": MAX_FILE_BYTES},
        )

    try:
        text = extract_text(extension, data).strip()
    except TextExtractionError:
        raise ApiError(
            400,
            f"无法从文件抽取文本（文件损坏或无文本层）：{filename}",
            code="product_facts_import_unreadable",
            params={"filename": filename},
        ) from None
    if not text:
        raise ApiError(
            400,
            f"无法从文件抽取文本（文件损坏或无文本层）：{filename}",
            code="product_facts_import_unreadable",
            params={"filename": filename},
        )
    extracted_chars = len(text)
    truncated = extracted_chars > MAX_EXTRACT_CHARS
    if truncated:
        text = text[:MAX_EXTRACT_CHARS]

    config = resolve_ai_config(db, settings)
    if not config.api_key:
        raise ApiError(
            409,
            "AI API Key 未配置：请先在本页上方保存"
            "（支持 OpenAI / Kimi 等 OpenAI 兼容接口），"
            "或设置环境变量 OPENAI_API_KEY 后重试",
            code="ai_key_not_configured",
        )

    user = f"产品资料文件名：{filename}\n\n产品资料全文：\n{text}"
    # 非法输出（无返回 / 形状校验不过）重试一次；仍失败 502，不落库
    for _attempt in range(2):
        candidate = complete_json(
            config, system=IMPORT_SYSTEM, user=user, max_tokens=IMPORT_MAX_TOKENS
        )
        facts = validate_facts(candidate)
        if facts is not None:
            return ProductFactsImportOutcome(
                product_facts=facts,
                extracted_chars=extracted_chars,
                truncated=truncated,
            )
    logger.warning("product-facts 导入两次均未通过校验 filename=%s", filename)
    raise ApiError(
        502,
        "资料解析失败：模型两次输出均未通过校验，请重试",
        code="product_facts_import_invalid_output",
    )
