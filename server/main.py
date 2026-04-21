"""
火山引擎方舟大模型代理后端：使用 Ark API Key 调用 Ark Chat Completions。
环境变量：ARK_API_KEY（仅后端读取，不可放前端）
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="XHS Coldstart LLM Proxy", version="1.0.0")

_cors = os.getenv("CORS_ALLOW_ORIGINS", "*")
_origins = [o.strip() for o in _cors.split(",") if o.strip()] if _cors != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateRequest(BaseModel):
    system_text: str = Field(..., description="系统/分析师指令全文")
    user_text: str = Field(..., description="用户侧提示词")
    model: Optional[str] = Field(None, description="模型 ID，默认读环境变量 ARK_MODEL_ID")


class GenerateResponse(BaseModel):
    ok: bool
    raw_text: str = ""
    error_message: str = ""
    error_code: str = ""
    request_id: str = ""
    debug: str = ""


def _map_exception(exc: Exception) -> tuple[str, str]:
    """返回 (user_message, error_code)"""
    try:
        from volcenginesdkarkruntime._exceptions import (
            ArkAPIConnectionError,
            ArkAPIStatusError,
            ArkAPITimeoutError,
            ArkAuthenticationError,
            ArkPermissionDeniedError,
            ArkRateLimitError,
        )

        if isinstance(exc, (ArkAuthenticationError, ArkPermissionDeniedError)):
            return "API配置异常，请联系管理员", "auth"
        if isinstance(exc, ArkRateLimitError):
            return "请求过于频繁，请稍后再试", "rate_limit"
        if isinstance(exc, ArkAPITimeoutError):
            return "网络异常，请检查网络后重试", "timeout"
        if isinstance(exc, ArkAPIConnectionError):
            return "网络异常，请检查网络后重试", "network"
        if isinstance(exc, ArkAPIStatusError):
            sc = int(getattr(exc, "status_code", 0) or 0)
            if sc in (401, 403):
                return "API配置异常，请联系管理员", "auth"
            if sc == 429:
                return "请求过于频繁，请稍后再试", "rate_limit"
            if sc == 408:
                return "网络异常，请检查网络后重试", "timeout"
            return "网络异常，请检查网络后重试", "api"
    except Exception:
        pass

    if isinstance(exc, httpx.TimeoutException):
        return "网络异常，请检查网络后重试", "timeout"
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, ConnectionError, OSError)):
        return "网络异常，请检查网络后重试", "network"

    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "throttl" in msg:
        return "请求过于频繁，请稍后再试", "rate_limit"
    if "401" in msg or "403" in msg or "unauthor" in msg or "permission" in msg:
        return "API配置异常，请联系管理员", "auth"
    return "网络异常，请检查网络后重试", "unknown"


def _read_http_timeout() -> httpx.Timeout:
    try:
        total = float(os.getenv("ARK_HTTP_READ_TIMEOUT", "180").strip())
    except Exception:
        total = 180.0
    total = max(30.0, min(total, 600.0))
    return httpx.Timeout(total, connect=15.0)


def _get_ark_client() -> Any:
    from volcenginesdkarkruntime import Ark

    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("missing_credentials")
    return Ark(api_key=api_key, timeout=_read_http_timeout(), max_retries=0)


def _invoke_once(client: Any, model: str, system_text: str, user_text: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        max_tokens=3200,
        temperature=0.2,
        stream=False,
    )
    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise RuntimeError("empty_output")
    msg = getattr(choices[0], "message", None)
    content = getattr(msg, "content", None) if msg is not None else None
    if not content or not str(content).strip():
        raise RuntimeError("empty_output")
    return str(content).strip()


def _invoke_stream(client: Any, model: str, system_text: str, user_text: str):
    """Yield text deltas from Ark streaming completion."""
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        max_tokens=3200,
        temperature=0.2,
        stream=True,
    )
    for chunk in stream:
        try:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            piece = getattr(delta, "content", None) if delta is not None else None
            if piece:
                yield str(piece)
        except Exception:
            continue


def _should_retry(exc: Exception) -> bool:
    try:
        from volcenginesdkarkruntime._exceptions import (
            ArkAPIConnectionError,
            ArkAPIStatusError,
            ArkAPITimeoutError,
            ArkAuthenticationError,
            ArkPermissionDeniedError,
            ArkRateLimitError,
        )

        if isinstance(exc, (ArkAuthenticationError, ArkPermissionDeniedError)):
            return False
        if isinstance(exc, (ArkAPITimeoutError, ArkAPIConnectionError, ArkRateLimitError)):
            return True
        if isinstance(exc, ArkAPIStatusError):
            sc = int(getattr(exc, "status_code", 0) or 0)
            if sc in (401, 403):
                return False
            if sc in (429, 500, 502, 503, 504):
                return True
        return False
    except Exception:
        return True


def _invoke_with_retries(model: str, system_text: str, user_text: str) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            client = _get_ark_client()
            return _invoke_once(client, model, system_text, user_text)
        except Exception as e:
            last_exc = e
            if isinstance(e, RuntimeError) and str(e) in ("missing_credentials", "empty_output"):
                raise
            if not _should_retry(e) or attempt >= 2:
                raise
            time.sleep(2**attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("empty_output")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/generate", response_model=GenerateResponse)
def generate(body: GenerateRequest, request: Request) -> GenerateResponse:
    rid = str(uuid.uuid4())
    debug_enabled = os.getenv("DEBUG_API", "").strip() in ("1", "true", "True", "yes", "YES")
    logging.info("generate start rid=%s from=%s", rid, getattr(request.client, "host", ""))

    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if token:
        if request.headers.get("X-Internal-Token", "") != token:
            raise HTTPException(status_code=401, detail="unauthorized")

    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        return GenerateResponse(
            ok=False,
            error_message="API配置异常，请联系管理员",
            error_code="missing_credentials",
            request_id=rid,
        )

    model = (body.model or os.getenv("ARK_MODEL_ID", "doubao-seed-2-0-pro-260215")).strip()
    try:
        raw = _invoke_with_retries(model, body.system_text, body.user_text)
        return GenerateResponse(ok=True, raw_text=raw, request_id=rid)
    except RuntimeError as e:
        if str(e) == "missing_credentials":
            return GenerateResponse(
                ok=False,
                error_message="API配置异常，请联系管理员",
                error_code="missing_credentials",
                request_id=rid,
            )
        if str(e) == "empty_output":
            return GenerateResponse(
                ok=False,
                error_message="生成失败，请重新尝试",
                error_code="empty_output",
                request_id=rid,
            )
        user_msg, code = _map_exception(e)
        logging.exception("generate failed rid=%s code=%s model=%s err=%s", rid, code, model, e)
        dbg = ""
        if debug_enabled or code == "unknown":
            dbg = json.dumps({"type": type(e).__name__, "message": str(e)}, ensure_ascii=False)
        return GenerateResponse(ok=False, error_message=user_msg, error_code=code, request_id=rid, debug=dbg)
    except Exception as e:
        user_msg, code = _map_exception(e)
        logging.exception("generate failed rid=%s code=%s model=%s err=%s", rid, code, model, e)
        dbg = ""
        if debug_enabled or code == "unknown":
            dbg = json.dumps({"type": type(e).__name__, "message": str(e)}, ensure_ascii=False)
        return GenerateResponse(ok=False, error_message=user_msg, error_code=code, request_id=rid, debug=dbg)


@app.post("/api/v1/generate_stream")
def generate_stream(body: GenerateRequest, request: Request):
    """
    SSE：event:data\n\n，每条 data 为 UTF-8 文本增量。
    结束时发送 event:done。
    """
    rid = str(uuid.uuid4())
    logging.info("generate_stream start rid=%s from=%s", rid, getattr(request.client, "host", ""))

    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if token:
        if request.headers.get("X-Internal-Token", "") != token:
            raise HTTPException(status_code=401, detail="unauthorized")

    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:

        def err_gen():
            yield "event: error\n"
            yield 'data: {"ok":false,"error_message":"API配置异常，请联系管理员","error_code":"missing_credentials"}\n\n'

        return StreamingResponse(err_gen(), media_type="text/event-stream")

    model = (body.model or os.getenv("ARK_MODEL_ID", "doubao-seed-2-0-pro-260215")).strip()

    def sse_gen():
        try:
            client = _get_ark_client()
            for piece in _invoke_stream(client, model, body.system_text, body.user_text):
                # SSE data 允许多行；此处单行 JSON 包裹以避免注入换行破坏协议
                payload = json.dumps({"ok": True, "delta": piece}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            yield "event: done\n"
            yield "data: {}\n\n"
        except Exception as e:
            user_msg, code = _map_exception(e)
            dbg = ""
            debug_enabled = os.getenv("DEBUG_API", "").strip() in ("1", "true", "True", "yes", "YES")
            if debug_enabled or code == "unknown":
                dbg = json.dumps({"type": type(e).__name__, "message": str(e)}, ensure_ascii=False)
            payload = json.dumps(
                {"ok": False, "error_message": user_msg, "error_code": code, "debug": dbg}, ensure_ascii=False
            )
            yield "event: error\n"
            yield f"data: {payload}\n\n"

    return StreamingResponse(sse_gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
