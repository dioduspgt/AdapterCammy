# adapter.py
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


UPSTREAM_CHAT_COMPLETIONS_URL = os.getenv("UPSTREAM_CHAT_COMPLETIONS_URL")
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY")

if not UPSTREAM_CHAT_COMPLETIONS_URL:
    raise RuntimeError(
        "Set UPSTREAM_CHAT_COMPLETIONS_URL, for example: "
        "https://your-real-api.example.com/v1/chat/completions"
    )

HOP_BY_HOP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

HOP_BY_HOP_RESPONSE_HEADERS = {
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


app = FastAPI(title="Chat Completions Adapter")


@app.on_event("startup")
async def startup_event() -> None:
    app.state.http = httpx.AsyncClient(timeout=None, http2=True)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await app.state.http.aclose()


def build_upstream_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}

    for key, value in request.headers.items():
        if key.lower() in HOP_BY_HOP_REQUEST_HEADERS:
            continue
        headers[key] = value

    # If the caller did not provide Authorization, fall back to env var.
    if "authorization" not in {k.lower() for k in headers} and UPSTREAM_API_KEY:
        headers["Authorization"] = f"Bearer {UPSTREAM_API_KEY}"

    return headers


def build_downstream_headers(upstream_response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}

    for key, value in upstream_response.headers.items():
        if key.lower() in HOP_BY_HOP_RESPONSE_HEADERS:
            continue
        headers[key] = value

    return headers


def inject_enable_thinking(payload: dict) -> dict:
    chat_template_kwargs = payload.get("chat_template_kwargs")

    if chat_template_kwargs is None:
        chat_template_kwargs = {}
    elif not isinstance(chat_template_kwargs, dict):
        raise HTTPException(
            status_code=400,
            detail="'chat_template_kwargs' must be an object when provided",
        )

    chat_template_kwargs["enable_thinking"] = False
    payload["chat_template_kwargs"] = chat_template_kwargs
    return payload


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Request body must be valid JSON"},
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "Request body must be a JSON object"},
        )

    payload = inject_enable_thinking(payload)
    stream = bool(payload.get("stream", False))

    headers = build_upstream_headers(request)
    client: httpx.AsyncClient = app.state.http

    # Preserve query parameters if any are present.
    params = list(request.query_params.multi_items())

    if stream:
        upstream_request = client.build_request(
            "POST",
            UPSTREAM_CHAT_COMPLETIONS_URL,
            headers=headers,
            json=payload,
            params=params,
        )
        upstream_response = await client.send(upstream_request, stream=True)

        async def body_iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_response.aiter_raw():
                    yield chunk
            finally:
                await upstream_response.aclose()

        response_headers = build_downstream_headers(upstream_response)
        media_type = upstream_response.headers.get("content-type", "text/event-stream")

        return StreamingResponse(
            body_iterator(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=media_type,
        )

    upstream_response = await client.post(
        UPSTREAM_CHAT_COMPLETIONS_URL,
        headers=headers,
        json=payload,
        params=params,
    )

    response_headers = build_downstream_headers(upstream_response)
    media_type = upstream_response.headers.get("content-type")

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=media_type,
    )