"""Orquestra a demo ao vivo: programa → casos → nota → notificação.

A demo é reexecutável: cada Iniciar/Recomeçar cancela a geração anterior
e limpa a mesa antes de gravar de novo.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sqlalchemy import delete, select

from app.db import Case, Notice, RunState, Segment, case_dict, notice_dict, session, utcnow
from app.factcheck import verify_claim
from app.labels import STAMP_LABEL
from app.settings import settings

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_run_id = 0


def load_programa() -> dict:
    path: Path = settings.programa_path
    return json.loads(path.read_text(encoding="utf-8"))


def _speed() -> float:
    """1.0 = tempo do roteiro. >1 acelera a apresentação."""
    return max(0.4, min(3.0, float(settings.demo_speed or 1.0)))


def status_snapshot() -> dict:
    programa = load_programa()
    with session() as db:
        state = db.get(RunState, 1)
        segments = list(db.scalars(select(Segment).order_by(Segment.id.desc()).limit(80)))
        cases = list(db.scalars(select(Case).order_by(Case.id.desc()).limit(20)))
        notices = list(db.scalars(select(Notice).order_by(Notice.id.desc()).limit(20)))
        live_web = bool(settings.openrouter_key)
        return {
            "running": bool(state.running) if state else False,
            "phase": state.phase if state else "idle",
            "last_error": state.last_error if state else "",
            "run_id": _run_id,
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


def _active(run_id: int) -> bool:
    return (not _stop.is_set()) and run_id == _run_id


def reset_demo() -> dict:
    """Para qualquer geração, limpa banco e volta a mesa para idle."""
    global _run_id, _thread
    with _lock:
        _run_id += 1
        cancelled = _run_id
        _stop.set()
        old = _thread
    if old and old.is_alive() and old is not threading.current_thread():
        old.join(timeout=1.5)
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
    with _lock:
        if _thread is old:
            _thread = None
    return {"ok": True, "run_id": cancelled, "cleared": True}


def start_demo() -> dict:
    """Sempre recomeça do zero: cancela anterior, limpa e sobe nova geração."""
    global _thread, _run_id
    reset_demo()
    with _lock:
        _run_id += 1
        run_id = _run_id
        _stop.clear()
        _thread = threading.Thread(
            target=_run_demo, args=(run_id,), name=f"demo-{run_id}", daemon=True
        )
        _thread.start()
    return {"ok": True, "run_id": run_id}


def stop_demo(wait: bool = False) -> dict:
    global _run_id
    with _lock:
        _run_id += 1
        _stop.set()
        old = _thread
    if wait and old and old.is_alive():
        old.join(timeout=1.5)
    _set_phase("parado", running=False)
    return {"ok": True}


def _set_phase(
    phase: str,
    running: bool | None = None,
    error: str | None = None,
    run_id: int | None = None,
) -> None:
    if run_id is not None and run_id != _run_id:
        return
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


def _run_demo(run_id: int) -> None:
    programa = load_programa()
    lines = programa.get("lines") or []
    speed = _speed()
    _set_phase("ouvindo", running=True, error="", run_id=run_id)
    started = time.time()
    try:
        for item in lines:
            if not _active(run_id):
                break
            target = float(item.get("t") or 0) / speed
            while _active(run_id) and time.time() - started < target:
                time.sleep(0.12)
            if not _active(run_id):
                break
            speaker = str(item.get("speaker") or "Programa")
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            with session() as db:
                if not _active(run_id):
                    break
                db.add(Segment(speaker=speaker, text=text))
                db.commit()
            claim = item.get("claim")
            if isinstance(claim, dict):
                _process_claim(
                    claim,
                    run_id,
                    programa=programa,
                    offset_s=float(item.get("t") or 0),
                )
                if not _active(run_id):
                    break
        if _active(run_id):
            _set_phase("concluido", running=False, run_id=run_id)
        elif run_id == _run_id:
            _set_phase("parado", running=False, run_id=run_id)
    except Exception as exc:
        if run_id == _run_id:
            _set_phase("erro", running=False, error=str(exc), run_id=run_id)


def _clock_from_offset(start_time: str, offset_s: float) -> str:
    raw = (start_time or "15:00").strip()
    try:
        hour_s, minute_s = raw.split(":", 1)
        total = int(hour_s) * 60 + int(minute_s) + int(offset_s // 60)
    except (TypeError, ValueError):
        total = 15 * 60 + int(offset_s // 60)
    total %= 24 * 60
    return f"{total // 60:02d}h{total % 60:02d}"


def _broadcast_for_claim(programa: dict, claim: dict, offset_s: float = 0) -> dict:
    base = dict(programa.get("broadcast") or {})
    claim_ctx = claim.get("broadcast") if isinstance(claim.get("broadcast"), dict) else {}
    merged = {**base, **claim_ctx}
    date = str(merged.get("date") or "").strip() or "10/11/2026"
    time_label = str(merged.get("time") or "").strip()
    if not time_label:
        time_label = _clock_from_offset(str(merged.get("start_time") or "15:00"), offset_s)
    return {
        "vehicle": str(merged.get("vehicle") or programa.get("station") or "Rádio local").strip(),
        "program": str(merged.get("program") or programa.get("title") or "Programa").strip(),
        "place": str(merged.get("place") or "Ribeirão Preto (SP)").strip(),
        "date": date,
        "time": time_label,
        "said_at": f"{date} · {time_label}",
    }


def _process_claim(
    claim: dict,
    run_id: int,
    programa: dict | None = None,
    offset_s: float = 0,
) -> None:
    if not _active(run_id):
        return
    quote = str(claim.get("quote") or "").strip()
    normalized = str(claim.get("normalized") or quote).strip()
    theme = str(claim.get("theme") or "").strip()
    kb_ids = [str(x) for x in (claim.get("kb_ids") or [])]
    if len(quote) < 8:
        return
    context = _broadcast_for_claim(programa or {}, claim, offset_s=offset_s)

    _set_phase("detectou", run_id=run_id)
    with session() as db:
        if not _active(run_id):
            return
        row = Case(
            quote=quote[:500],
            normalized=normalized[:500],
            theme=theme[:40],
            status="processando",
            origin="demo",
            vehicle=context["vehicle"][:120],
            place=context["place"][:120],
            said_at=context["said_at"][:80],
            program=context["program"][:160],
            pipeline_json=json.dumps(
                [{"step": "detectou", "ok": True, "detail": "afirmação extraída do áudio/texto"}],
                ensure_ascii=False,
            ),
        )
        db.add(row)
        db.commit()
        case_id = row.id

    if not _active(run_id):
        return
    _set_phase("checando", run_id=run_id)
    # Offline KB é o caminho estável da apresentação; web ao vivo só se a chave existir
    # e a geração continuar ativa depois.
    verdict = verify_claim(
        quote,
        normalized,
        theme=theme,
        kb_ids=kb_ids,
        use_live_web=bool(settings.openrouter_key),
        context=context,
    )
    if not _active(run_id):
        return

    pipeline = list(verdict.get("pipeline") or [])
    pipeline.insert(0, {"step": "detectou", "ok": True, "detail": "afirmação extraída do programa"})
    pipeline.append({"step": "nota", "ok": True, "detail": "nota de crise gerada"})

    _set_phase("notificando", run_id=run_id)
    with session() as db:
        if not _active(run_id):
            return
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
        row.vehicle = str(verdict.get("vehicle") or context["vehicle"])[:120]
        row.place = str(verdict.get("place") or context["place"])[:120]
        row.said_at = str(verdict.get("said_at") or context["said_at"])[:80]
        row.program = str(verdict.get("program") or context["program"])[:160]
        label = STAMP_LABEL.get(row.status, row.status)
        priority = verdict.get("response_priority") or "media"
        subject = ""
        for line in (row.note or "").splitlines():
            if line.casefold().startswith("assunto:"):
                subject = line.split(":", 1)[1].strip()
                break
        title = f"[{label} · {priority}] {subject or quote[:80]}"
        body = (row.note or row.summary or "").strip()
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
    if _active(run_id):
        _set_phase("ouvindo", run_id=run_id)
