"""Orquestra a demo ao vivo: programa → casos → nota → notificação."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sqlalchemy import delete, select

from app.db import Case, Notice, RunState, Segment, case_dict, notice_dict, session, utcnow
from app.factcheck import STAMP_LABEL, verify_claim
from app.settings import settings

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def load_programa() -> dict:
    path: Path = settings.programa_path
    return json.loads(path.read_text(encoding="utf-8"))


def status_snapshot() -> dict:
    programa = load_programa()
    with session() as db:
        state = db.get(RunState, 1)
        segments = list(db.scalars(select(Segment).order_by(Segment.id.asc()).limit(80)))
        cases = list(db.scalars(select(Case).order_by(Case.id.desc()).limit(20)))
        notices = list(db.scalars(select(Notice).order_by(Notice.id.desc()).limit(20)))
        live_web = bool(settings.openrouter_key)
        return {
            "running": bool(state.running) if state else False,
            "phase": state.phase if state else "idle",
            "last_error": state.last_error if state else "",
            "city": settings.city_name,
            "live_web": live_web,
            "fact_model": settings.fact_model if live_web else "base-oficial-kb",
            "programa": {
                "title": programa.get("title"),
                "station": programa.get("station"),
                "disclaimer": programa.get("disclaimer"),
            },
            "segments": [
                {
                    "id": row.id,
                    "speaker": row.speaker,
                    "text": row.text,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                }
                for row in segments
            ],
            "cases": [case_dict(row) for row in cases],
            "notices": [notice_dict(row) for row in notices],
            "stamps": STAMP_LABEL,
        }


def reset_demo() -> None:
    stop_demo(wait=True)
    with session() as db:
        db.execute(delete(Notice))
        db.execute(delete(Case))
        db.execute(delete(Segment))
        state = db.get(RunState, 1)
        if state:
            state.running = 0
            state.phase = "idle"
            state.last_error = ""
            state.updated_at = utcnow()
        db.commit()


def start_demo() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run_demo, name="demo-programa", daemon=True)
        _thread.start()


def stop_demo(wait: bool = False) -> None:
    _stop.set()
    thread = _thread
    if wait and thread and thread.is_alive():
        thread.join(timeout=2)


def _set_phase(phase: str, running: bool | None = None, error: str | None = None) -> None:
    with session() as db:
        state = db.get(RunState, 1)
        if state is None:
            return
        state.phase = phase
        if running is not None:
            state.running = 1 if running else 0
        if error is not None:
            state.last_error = error[:800]
        state.updated_at = utcnow()
        db.commit()


def _run_demo() -> None:
    programa = load_programa()
    lines = programa.get("lines") or []
    _set_phase("ouvindo", running=True, error="")
    started = time.time()
    try:
        for item in lines:
            if _stop.is_set():
                break
            target = float(item.get("t") or 0)
            while not _stop.is_set() and time.time() - started < target:
                time.sleep(0.15)
            if _stop.is_set():
                break
            speaker = str(item.get("speaker") or "Programa")
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            with session() as db:
                db.add(Segment(speaker=speaker, text=text))
                db.commit()
            claim = item.get("claim")
            if isinstance(claim, dict):
                _process_claim(claim)
        if not _stop.is_set():
            _set_phase("concluido", running=False)
        else:
            _set_phase("parado", running=False)
    except Exception as exc:
        _set_phase("erro", running=False, error=str(exc))


def _process_claim(claim: dict) -> None:
    quote = str(claim.get("quote") or "").strip()
    normalized = str(claim.get("normalized") or quote).strip()
    theme = str(claim.get("theme") or "").strip()
    kb_ids = [str(x) for x in (claim.get("kb_ids") or [])]
    if len(quote) < 8:
        return

    _set_phase("detectou")
    with session() as db:
        row = Case(
            quote=quote[:500],
            normalized=normalized[:500],
            theme=theme[:40],
            status="processando",
            origin="demo",
            pipeline_json=json.dumps(
                [{"step": "detectou", "ok": True, "detail": "afirmação extraída do áudio/texto"}],
                ensure_ascii=False,
            ),
        )
        db.add(row)
        db.commit()
        case_id = row.id

    _set_phase("checando")
    verdict = verify_claim(
        quote,
        normalized,
        theme=theme,
        kb_ids=kb_ids,
        use_live_web=bool(settings.openrouter_key),
    )
    pipeline = list(verdict.get("pipeline") or [])
    pipeline.insert(0, {"step": "detectou", "ok": True, "detail": "afirmação extraída do programa"})
    pipeline.append({"step": "nota", "ok": True, "detail": "nota de crise gerada"})

    _set_phase("notificando")
    with session() as db:
        row = db.get(Case, case_id)
        if row is None:
            return
        row.status = verdict["status"]
        row.theme = verdict.get("theme") or theme
        row.confidence = float(verdict.get("confidence") or 0)
        row.summary = str(verdict.get("summary") or "")
        row.note = str(verdict.get("note") or "")
        row.missing = str(verdict.get("missing") or "")
        row.needs_response = 1 if verdict.get("needs_response") else 0
        row.sources_json = json.dumps(verdict.get("sources") or [], ensure_ascii=False)
        label = STAMP_LABEL.get(row.status, row.status)
        priority = verdict.get("response_priority") or "media"
        title = f"[{label}] {quote[:90]}"
        body = (
            f"Prioridade: {priority}\n"
            f"Precisa resposta: {'sim' if row.needs_response else 'não'}\n\n"
            f"{row.note}\n\n"
            f"Resumo: {row.summary}"
        )
        db.add(
            Notice(
                case_id=row.id,
                channel="sala_imprensa",
                title=title[:200],
                body=body[:4000],
            )
        )
        row.notified = 1
        pipeline.append(
            {
                "step": "notificou",
                "ok": True,
                "detail": "alerta enviado à Sala de Imprensa do município",
            }
        )
        row.pipeline_json = json.dumps(pipeline, ensure_ascii=False)
        db.commit()
    _set_phase("ouvindo")
