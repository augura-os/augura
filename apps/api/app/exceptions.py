"""Domain exception rendered as the unified error envelope."""

from __future__ import annotations


class ApiError(Exception):
    """Raised anywhere in the stack; the exception handler in main.py turns
    it into ``{success: false, data: null, message}`` with ``status_code``.

    ``code``/``params`` 是 i18n 结构化字段：前端按 code 查 ``error.<code>``
    模板并用 params 插值；``message`` 保留中文旧文案作 legacy 兜底。
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.params: dict[str, object] = params or {}
