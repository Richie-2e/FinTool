from __future__ import annotations


class APIError(Exception):
    """Raise this anywhere in a router; main.py registers the handler."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str,
        doc_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.doc_id = doc_id
