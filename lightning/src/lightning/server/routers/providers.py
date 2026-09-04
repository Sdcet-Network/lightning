from fastapi import APIRouter

from lightning.server.db import get_conn
from lightning.server.dispatcher_supervisor import get_supervisor
from lightning.server.model_providers import list_providers, replace_providers
from lightning.server.models import ModelProvider

router = APIRouter(tags=["settings"])


@router.get("/settings/providers", response_model=list[ModelProvider])
def get_settings_providers():
    with get_conn() as conn:
        return list_providers(conn)


@router.put("/settings/providers", response_model=list[ModelProvider])
def put_settings_providers(body: list[ModelProvider]):
    with get_conn() as conn:
        replace_providers(conn, body)
        providers = list_providers(conn)
    supervisor = get_supervisor()
    if supervisor is not None:
        supervisor.sync(providers)
    return providers
