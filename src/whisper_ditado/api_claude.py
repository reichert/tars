"""Rotas do catálogo de modelos do Claude, incluídas no router de `/api` (herdam a autenticação).

O GET só devolve o cache; a sonda (vários `claude` em sequência, alguns segundos) fica no POST
e roda no pool de threads para não travar o loop.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from whisper_ditado.claude_models import ClaudeModels, ModelCatalogError, RefreshInterrupted


def claude_router(ctx) -> APIRouter:
    router = APIRouter()
    catalog = ctx.claude_models or ClaudeModels(ctx.store, lambda: ctx.settings.get("claude_bin"))

    @router.get("/claude/models")
    def get_models() -> dict:
        return catalog.public()

    @router.post("/claude/models/refresh")
    async def refresh_models() -> dict:
        try:
            return await run_in_threadpool(catalog.refresh)  # respeita `catalog.stopping` (desligamento)
        except (ModelCatalogError, RefreshInterrupted) as exc:
            reason = str(exc) or "o serviço está encerrando"
            raise HTTPException(502, f"Não foi possível consultar os modelos do Claude: {reason}") from exc

    return router
