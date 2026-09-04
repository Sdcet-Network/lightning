from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from lightning import __version__
from lightning.server import db
from lightning.server.db import get_conn
from lightning.server.dispatcher_supervisor import get_supervisor, shutdown_supervisor
from lightning.server.model_providers import list_providers
from lightning.server.routers import export, hints, intents, projects, providers, settings

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.configure(db.DEFAULT_DB)
    supervisor = get_supervisor()
    if supervisor is not None:
        with get_conn() as conn:
            supervisor.sync(list_providers(conn))
    yield
    shutdown_supervisor()


app = FastAPI(
    title="闪电",
    description="Fact-graph based collaborative exploration protocol",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(settings.router)
app.include_router(providers.router)
app.include_router(projects.router)
app.include_router(hints.router)
app.include_router(intents.router)
app.include_router(export.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
