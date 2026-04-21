"""
Streamlit 侧调用本地 FastAPI 代理生成报告（不含任何火山引擎密钥）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterator, Optional

import requests


class LlmUserError(Exception):
    """携带已对用户友好的中文提示。"""

    def __init__(self, message: str, *, error_code: str = "", request_id: str = "", debug: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.request_id = request_id
        self.debug = debug


def _normalize_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def call_llm_backend(
    base_url: str,
    internal_token: str,
    system_text: str,
    user_text: str,
    *,
    model: Optional[str] = None,
    on_retry: Optional[Callable[[str], None]] = None,
) -> str:
    """
    调用后端 /api/v1/generate；连接超时 15s、读超时 180s；最多 2 次指数退避重试（共 3 次）。
    """
    root = _normalize_base(base_url)
    if not root:
        raise LlmUserError("API配置异常，请联系管理员")

    endpoint = f"{root}/api/v1/generate"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if internal_token:
        headers["X-Internal-Token"] = internal_token

    body: Dict[str, Any] = {"system_text": system_text, "user_text": user_text}
    if model:
        body["model"] = model

    last_msg = "网络异常，请检查网络后重试"
    for attempt in range(3):
        try:
            r = requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=(15, 180),
            )
        except requests.Timeout:
            if attempt < 2:
                if on_retry:
                    try:
                        on_retry("请求超时，正在重试...")
                    except Exception:
                        pass
                time.sleep(2**attempt)
                continue
            raise LlmUserError("网络异常，请检查网络后重试") from None
        except requests.RequestException:
            last_msg = "网络异常，请检查网络后重试"
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise LlmUserError(last_msg) from None

        if r.status_code == 429:
            last_msg = "请求过于频繁，请稍后再试"
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise LlmUserError(last_msg)

        if r.status_code >= 500:
            last_msg = "网络异常，请检查网络后重试"
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise LlmUserError(last_msg)

        try:
            data = r.json()
        except Exception:
            snippet = (r.text or "")[:1200]
            raise LlmUserError("生成失败，请重新尝试", error_code="bad_json", debug=snippet)

        if not isinstance(data, dict):
            raise LlmUserError("生成失败，请重新尝试")

        if r.status_code == 401:
            rid = str(data.get("request_id") or "")
            dbg = str(data.get("debug") or "")
            raise LlmUserError("API配置异常，请联系管理员", error_code="unauthorized", request_id=rid, debug=dbg)

        if not data.get("ok"):
            em = str(data.get("error_message") or "").strip()
            code = str(data.get("error_code") or "")
            rid = str(data.get("request_id") or "")
            dbg = str(data.get("debug") or "")
            if em:
                raise LlmUserError(em, error_code=code, request_id=rid, debug=dbg)
            if code in ("auth", "missing_credentials"):
                raise LlmUserError("API配置异常，请联系管理员", error_code=code, request_id=rid, debug=dbg)
            if code == "rate_limit":
                raise LlmUserError("请求过于频繁，请稍后再试", error_code=code, request_id=rid, debug=dbg)
            raise LlmUserError("生成失败，请重新尝试", error_code=code, request_id=rid, debug=dbg)

        raw = str(data.get("raw_text") or "").strip()
        if not raw:
            raise LlmUserError("生成失败，请重新尝试")
        return raw

    raise LlmUserError(last_msg)


def call_llm_backend_stream(
    base_url: str,
    internal_token: str,
    system_text: str,
    user_text: str,
    *,
    model: Optional[str] = None,
    on_retry: Optional[Callable[[str], None]] = None,
) -> Iterator[str]:
    """
    调用后端 /api/v1/generate_stream（SSE）。逐段 yield 文本增量（拼接即为完整输出）。
    """
    root = _normalize_base(base_url)
    if not root:
        raise LlmUserError("API配置异常，请联系管理员")

    endpoint = f"{root}/api/v1/generate_stream"
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if internal_token:
        headers["X-Internal-Token"] = internal_token

    body: Dict[str, Any] = {"system_text": system_text, "user_text": user_text}
    if model:
        body["model"] = model

    last_msg = "网络异常，请检查网络后重试"
    for attempt in range(3):
        try:
            with requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                stream=True,
                timeout=(15, 600),
            ) as r:
                if r.status_code == 401:
                    raise LlmUserError("API配置异常，请联系管理员", error_code="unauthorized")
                if r.status_code >= 400:
                    txt = (r.text or "")[:1200]
                    raise LlmUserError("生成失败，请重新尝试", error_code="http", debug=txt)

                current_event = ""
                for raw_line in r.iter_lines(decode_unicode=True):
                    if raw_line is None:
                        continue
                    line = str(raw_line).strip("\r")
                    if line.startswith("event:"):
                        current_event = line[len("event:") :].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload:
                        continue
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    if current_event == "error" or (isinstance(obj, dict) and obj.get("ok") is False):
                        em = str((obj or {}).get("error_message") or "").strip()
                        code = str((obj or {}).get("error_code") or "")
                        dbg = str((obj or {}).get("debug") or "")
                        raise LlmUserError(em or "生成失败，请重新尝试", error_code=code, debug=dbg)
                    if isinstance(obj, dict) and obj.get("ok") is True:
                        d = str(obj.get("delta") or "")
                        if d:
                            yield d
                    if current_event == "done":
                        return
                    current_event = ""
                return
        except LlmUserError:
            raise
        except requests.Timeout:
            if attempt < 2:
                if on_retry:
                    try:
                        on_retry("请求超时，正在重试...")
                    except Exception:
                        pass
                time.sleep(2**attempt)
                continue
            raise LlmUserError("网络异常，请检查网络后重试") from None
        except requests.RequestException:
            last_msg = "网络异常，请检查网络后重试"
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise LlmUserError(last_msg) from None

    raise LlmUserError(last_msg)
