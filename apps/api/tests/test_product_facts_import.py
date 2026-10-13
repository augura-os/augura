"""POST /settings/product-facts/import：产品资料文件 → LLM 解析 product_facts。

直接调路由函数（test_upload_limits 同风格）；LLM 与 AI 配置全部 mock。
docx 用 zipfile 现造最小文件；pdf 覆盖扩展名接纳（抽取层 mock）与
pypdf 真实抽取（现场拼最小 PDF）两条路径。全部文件名/内容用虚构名。
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import ApiError
from app.services import product_facts_import
from app.services.product_facts_import import (
    MAX_EXTRACT_CHARS,
    MAX_FILE_BYTES,
    extract_text,
)
from app.services.settings import AIConfig, resolve_product_facts

_FAKE_AI_CONFIG = AIConfig(
    api_key="sk-fake",
    base_url="",
    vision_model="fake-vision-v1",
    embedding_model="",
)

# 虚构产品事实（LLM mock 返回；不与任何真实产品对应）
_FAKE_LLM_OUTPUT = {
    "core_loop": "拼接浮岛地块，放置采收光尘",
    "selling_points": ["单手竖屏", "离线也有收益"],
    "must_show": ["真实拼接动画"],
}


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


@pytest.fixture()
def llm_mocks(monkeypatch) -> None:  # noqa: ANN001
    """complete_json 返回合法虚构事实；resolve_ai_config 返回带 key 假配置。"""
    monkeypatch.setattr(
        product_facts_import, "complete_json", lambda *a, **k: dict(_FAKE_LLM_OUTPUT)
    )
    monkeypatch.setattr(
        product_facts_import, "resolve_ai_config", lambda _db, _s: _FAKE_AI_CONFIG
    )


def _import(db: Session, upload: UploadFile):  # noqa: ANN202
    from app.api.routes.settings import import_product_facts_file

    return import_product_facts_file(db, get_settings(), upload)


def _minimal_docx(paragraphs: list[str]) -> bytes:
    """zipfile 现造最小 docx：word/document.xml 里 w:p 分段、w:t 文本。"""
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>",
        )
    return buffer.getvalue()


def _minimal_pdf(text: str) -> bytes:
    """现场拼最小单页 PDF（Helvetica 文本行），pypdf 可真实抽取。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return out.getvalue()


class TestMdRoundtrip:
    def test_md_upload_parse_roundtrip(
        self, db_session: Session, llm_mocks: None
    ) -> None:
        content = "# 虚构产品：浮岛光尘\n\n核心循环：拼接浮岛地块，放置采收光尘。\n"
        result = _import(db_session, _upload("FAKE-facts-brief.md", content.encode()))
        assert result.data is not None
        facts = result.data.product_facts
        # LLM 只返回三键，响应规整为固定六键（缺省补空值）
        assert facts["core_loop"] == "拼接浮岛地块，放置采收光尘"
        assert facts["selling_points"] == ["单手竖屏", "离线也有收益"]
        assert facts["must_show"] == ["真实拼接动画"]
        assert facts["forbidden_claims"] == []
        assert facts["compliance_limits"] == []
        assert facts["success_metric_defs"] == []
        assert result.data.extracted_chars == len(content.strip())
        assert result.data.truncated is False
        # 不落库：解析结果只回前端，保存走正常 PUT
        assert resolve_product_facts(db_session) == {}

    def test_txt_extension_accepted(
        self, db_session: Session, llm_mocks: None
    ) -> None:
        result = _import(db_session, _upload("FAKE-facts-note.txt", "虚构文本".encode()))
        assert result.data is not None
        assert result.data.product_facts["core_loop"] == "拼接浮岛地块，放置采收光尘"

    def test_truncation_over_20000_chars(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, str] = {}

        def _fake_complete_json(config, *, system, user, max_tokens, **_kw):  # noqa: ANN001, ANN202
            captured["user"] = user
            return dict(_FAKE_LLM_OUTPUT)

        monkeypatch.setattr(product_facts_import, "complete_json", _fake_complete_json)
        monkeypatch.setattr(
            product_facts_import, "resolve_ai_config", lambda _db, _s: _FAKE_AI_CONFIG
        )
        content = "虚" * (MAX_EXTRACT_CHARS + 500)
        result = _import(db_session, _upload("FAKE-facts-long.md", content.encode()))
        assert result.data is not None
        assert result.data.truncated is True
        assert result.data.extracted_chars == MAX_EXTRACT_CHARS + 500
        # 进 prompt 的正文被截到上限（user 里还有文件名前缀行）
        assert captured["user"].endswith("虚" * MAX_EXTRACT_CHARS)


class TestDocx:
    def test_minimal_docx_roundtrip(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, str] = {}

        def _fake_complete_json(config, *, system, user, max_tokens, **_kw):  # noqa: ANN001, ANN202
            captured["user"] = user
            return dict(_FAKE_LLM_OUTPUT)

        monkeypatch.setattr(product_facts_import, "complete_json", _fake_complete_json)
        monkeypatch.setattr(
            product_facts_import, "resolve_ai_config", lambda _db, _s: _FAKE_AI_CONFIG
        )
        data = _minimal_docx(["虚构段落一：浮岛", "虚构段落二：光尘"])
        result = _import(db_session, _upload("FAKE-facts-doc.docx", data))
        assert result.data is not None
        # w:t 拼接、w:p 分段换行
        assert "虚构段落一：浮岛\n虚构段落二：光尘" in captured["user"]
        assert result.data.product_facts["core_loop"] == "拼接浮岛地块，放置采收光尘"

    def test_broken_docx_400(self, db_session: Session, llm_mocks: None) -> None:
        with pytest.raises(ApiError) as exc_info:
            _import(db_session, _upload("FAKE-facts-broken.docx", b"not a zip"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "product_facts_import_unreadable"


class TestPdf:
    def test_pdf_extension_accepted(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, llm_mocks: None
    ) -> None:
        # 扩展名接纳 + 分派：抽取层 mock，不依赖具体 PDF 字节
        monkeypatch.setattr(
            product_facts_import, "_extract_pdf", lambda _data: "虚构PDF文本"
        )
        result = _import(db_session, _upload("FAKE-facts-pdf.pdf", b"%PDF-fake"))
        assert result.data is not None
        assert result.data.extracted_chars == len("虚构PDF文本")

    def test_pdf_real_extraction_with_pypdf(self) -> None:
        text = extract_text(".pdf", _minimal_pdf("FAKE prism rover facts"))
        assert "FAKE prism rover facts" in text


class TestFileValidation:
    def test_bad_extension_400(self, db_session: Session, llm_mocks: None) -> None:
        with pytest.raises(ApiError) as exc_info:
            _import(db_session, _upload("FAKE-facts-sheet.xlsx", b"x"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "product_facts_import_unsupported_type"
        assert exc_info.value.params["filename"] == "FAKE-facts-sheet.xlsx"

    def test_oversize_400(self, db_session: Session, llm_mocks: None) -> None:
        with pytest.raises(ApiError) as exc_info:
            _import(
                db_session,
                _upload("FAKE-facts-huge.md", b"x" * (MAX_FILE_BYTES + 1)),
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "product_facts_import_too_large"
        assert exc_info.value.params["limit_bytes"] == MAX_FILE_BYTES

    def test_empty_file_400(self, db_session: Session, llm_mocks: None) -> None:
        with pytest.raises(ApiError) as exc_info:
            _import(db_session, _upload("FAKE-facts-empty.md", b""))
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "product_facts_import_empty_file"


class TestLlmFailures:
    def test_invalid_output_twice_502(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"n": 0}

        def _always_invalid(*_a, **_k):  # noqa: ANN202
            calls["n"] += 1
            return {"core_loop": ["类型错了"]}  # core_loop 应为 str

        monkeypatch.setattr(product_facts_import, "complete_json", _always_invalid)
        monkeypatch.setattr(
            product_facts_import, "resolve_ai_config", lambda _db, _s: _FAKE_AI_CONFIG
        )
        with pytest.raises(ApiError) as exc_info:
            _import(db_session, _upload("FAKE-facts-retry.md", "虚构内容".encode()))
        assert exc_info.value.status_code == 502
        assert exc_info.value.code == "product_facts_import_invalid_output"
        assert calls["n"] == 2  # 重试一次后放弃

    def test_invalid_then_valid_succeeds(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        responses = iter([{"unknown_key": 1}, dict(_FAKE_LLM_OUTPUT)])
        monkeypatch.setattr(
            product_facts_import, "complete_json", lambda *a, **k: next(responses)
        )
        monkeypatch.setattr(
            product_facts_import, "resolve_ai_config", lambda _db, _s: _FAKE_AI_CONFIG
        )
        result = _import(db_session, _upload("FAKE-facts-flaky.md", "虚构内容".encode()))
        assert result.data is not None
        assert result.data.product_facts["core_loop"] == "拼接浮岛地块，放置采收光尘"

    def test_ai_key_missing_409(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            product_facts_import,
            "resolve_ai_config",
            lambda _db, _s: AIConfig(
                api_key="", base_url="", vision_model="", embedding_model=""
            ),
        )
        with pytest.raises(ApiError) as exc_info:
            _import(db_session, _upload("FAKE-facts-nokey.md", "虚构内容".encode()))
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ai_key_not_configured"


class TestValidateFacts:
    def test_non_dict_rejected(self) -> None:
        assert product_facts_import.validate_facts(["core_loop"]) is None

    def test_none_rejected(self) -> None:
        assert product_facts_import.validate_facts(None) is None

    def test_unknown_key_rejected(self) -> None:
        assert product_facts_import.validate_facts({"category": "match9"}) is None

    def test_wrong_list_item_type_rejected(self) -> None:
        payload = {"must_show": ["真实拼接动画", 42]}
        assert product_facts_import.validate_facts(payload) is None

    def test_missing_keys_default_empty(self) -> None:
        facts = product_facts_import.validate_facts({"core_loop": "拼接浮岛地块"})
        assert facts is not None
        assert facts["core_loop"] == "拼接浮岛地块"
        assert facts["selling_points"] == []

    def test_null_values_treated_as_empty(self) -> None:
        # 模型常用 null 表达「资料未提及」——视为空值而非非法
        facts = product_facts_import.validate_facts(
            {"core_loop": None, "selling_points": ["单手竖屏"], "must_show": None}
        )
        assert facts is not None
        assert facts["core_loop"] == ""
        assert facts["selling_points"] == ["单手竖屏"]
        assert facts["must_show"] == []
