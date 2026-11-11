from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import init_db
from app.engine import load_programa, reset_demo, start_demo, status_snapshot, stop_demo
from app.factcheck import verify_claim
from app.kb import load_kb, search_kb
from app.settings import settings

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    load_kb()
    yield
    stop_demo()


app = FastAPI(title="Radar Municipal · Ribeirão Preto", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "city": settings.city_name,
        "live_web": bool(settings.openrouter_key),
        "fact_model": settings.fact_model if settings.openrouter_key else "base-oficial-kb",
    }


@app.get("/api/estado")
def estado() -> dict:
    return status_snapshot()


@app.get("/api/kb")
def kb() -> dict:
    data = load_kb()
    return {
        "city": data.get("city"),
        "updated_at": data.get("updated_at"),
        "source_portal": data.get("source_portal"),
        "facts": data.get("facts") or [],
    }


@app.get("/api/programa")
def programa() -> dict:
    return load_programa()


@app.post("/api/demo/iniciar")
def demo_iniciar() -> dict:
    reset_demo()
    start_demo()
    return {"ok": True}


@app.post("/api/demo/parar")
def demo_parar() -> dict:
    stop_demo()
    return {"ok": True}


@app.post("/api/demo/reiniciar")
def demo_reiniciar() -> dict:
    reset_demo()
    return {"ok": True}


class ManualBody(BaseModel):
    text: str = Field(min_length=12, max_length=2000)


@app.post("/api/checar")
def checar_manual(body: ManualBody) -> dict:
    text = " ".join(body.text.split())
    matches = search_kb(text)
    verdict = verify_claim(
        text,
        text,
        theme=matches[0]["theme"] if matches else "outro",
        kb_ids=[m["id"] for m in matches[:3]],
        use_live_web=bool(settings.openrouter_key),
    )
    return {"quote": text, **verdict}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/metodo")
def metodo() -> FileResponse:
    return FileResponse(STATIC / "metodo.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
