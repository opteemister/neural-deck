"""Same-origin chat sessions; deliberately separate from the public provider /v1 API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..chat.service import ChatService, ChatTurnRequest
from ..container import AppContainer
from ..errors import AppError
from .dependencies import get_runtime
from .management import success

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
Runtime = Annotated[AppContainer, Depends(get_runtime)]


def service(runtime: AppContainer) -> ChatService:
    if runtime.chat_service is None:
        raise AppError("chat_unavailable", "Сервис чата недоступен", status_code=503)
    return runtime.chat_service


@router.get("")
async def conversations(request: Request, runtime: Runtime) -> JSONResponse:
    return success(request, await service(runtime).list_conversations())


@router.delete("/{conversation_id}")
async def delete(conversation_id: str, request: Request, runtime: Runtime) -> JSONResponse:
    return success(request, await service(runtime).delete(conversation_id))


@router.get("/{conversation_id}")
async def conversation(conversation_id: str, request: Request, runtime: Runtime) -> JSONResponse:
    return success(request, await service(runtime).get(conversation_id))


@router.post("/{conversation_id}/turns")
async def send(
    conversation_id: str, payload: ChatTurnRequest, request: Request, runtime: Runtime
) -> JSONResponse:
    return success(request, await service(runtime).start(conversation_id, payload))


@router.post("/{conversation_id}/stop")
async def stop(conversation_id: str, request: Request, runtime: Runtime) -> JSONResponse:
    return success(request, await service(runtime).stop(conversation_id))


@router.get("/{conversation_id}/assets/{asset_id}")
async def attachment(conversation_id: str, asset_id: str, runtime: Runtime) -> Response:
    content, media_type, _ = await service(runtime).tools.asset(conversation_id, asset_id)
    return Response(content, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})
