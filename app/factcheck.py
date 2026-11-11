"""Checagem municipal + nota de crise sóbria."""

from __future__ import annotations

import json
import re

import httpx

from app.kb import kb_sources, search_kb
from app.labels import STAMP_LABEL
from app.settings import settings

CLOSED = (
    "sustentado",
    "sustentado_ressalva",
    "nao_e_bem_assim",
    "exagerado",
    "distorcido",
    "contradito",
    "sem_prova",
)
OPEN = (
    "sem_prova_publica",
    "versoes_em_disputa",
    "indicio",
    "dados_incompletos",
    "audio_confuso",
    "sem_evidencia",
    "inconclusivo",
)
ALLOWED = set(CLOSED + OPEN + ("promessa",))
NEEDS_SOURCE = set(CLOSED)

CRISIS_SYSTEM = """Você é especialista em comunicação de crise e assessoria de imprensa do poder público municipal brasileiro.
Cidade: Ribeirão Preto (SP).
Tom: sóbrio, factual, institucional — linguagem de nota oficial / assessoria. Sem ataque, sem marketing eleitoral, sem a palavra fake, sem a palavra 'contradito'.
Responda SÓ JSON:
{
  "status":"sustentado|sustentado_ressalva|nao_e_bem_assim|exagerado|distorcido|contradito|sem_prova|sem_prova_publica|versoes_em_disputa|indicio|dados_incompletos|audio_confuso|promessa",
  "confidence":0.0,
  "summary":"até 280 caracteres com o juízo factual",
  "missing":"o que falta para cravar, ou vazio",
  "needs_response":true,
  "response_priority":"alta|media|baixa|nenhuma",
  "note":"NOTA À IMPRENSA pronta para enviar após validação humana. Formato obrigatório abaixo.",
  "theme":"saude|agua|transporte|limpeza|atendimento|outro",
  "sources":[{"title":"...","url":"https://...","kind":"oficial|checagem|imprensa","excerpt":"trecho curto"}]
}

Significado dos status (código interno → juízo público):
- sustentado = Correto
- sustentado_ressalva = Correto, com ressalva
- nao_e_bem_assim = Impreciso
- exagerado = Superestimado
- distorcido = Descontextualizado
- contradito = Não procede
- sem_prova = Sem registro oficial
- sem_prova_publica = Sem registro público
- versoes_em_disputa = Versões em disputa
- indicio = Indício
- dados_incompletos = Dados incompletos
- audio_confuso = Registro inconsistente
- promessa = Compromisso anunciado

Formato obrigatório do campo note:
NOTA À IMPRENSA — Prefeitura de Ribeirão Preto
Assunto: <uma linha>

Referência da veiculação:
Veículo: <nome do veículo>
Programa/espaço: <programa ou manchete>
Data: <dd/mm/aaaa>
Horário: <HHhMM>
Local: <cidade/UF>

<2 a 4 parágrafos curtos em terceira pessoa, tom institucional, sem bullet de orientação interna>
Fonte oficial: <título> — <url https>
Assessoria de Comunicação / Prefeitura Municipal de Ribeirão Preto

Regras:
- A note NÃO é briefing interno. É o texto que a assessoria poderia colar no WhatsApp/e-mail da imprensa depois de validar.
- Não escreva 'sugestão', 'recomenda-se', 'orientação' nem 'posicionamento sugerido'.
- Não use fake nem contradito no texto da nota. Use 'não procede', 'impreciso', 'descontextualizado' etc.
- Não ataque o veículo nem o ouvinte.
- Inclua SEMPRE o bloco Referência da veiculação com veículo, data, horário e local recebidos no contexto.
- Carimbo fechado só com URL https nas sources OU com fato da base oficial já listado abaixo (aí repita a URL da base).
- Se a fala não procede perante a base oficial com URL, use contradito (rótulo público: Não procede) ou distorcido.
- Se a fala confirma a base, use sustentado.
- needs_response=true quando o conteúdo exige posicionamento público.
- Prefira portal ribeiraopreto.sp.gov.br, SAERP, RP Mobi e páginas oficiais.
- Não invente URL. Só use URLs recebidas no contexto ou devolvidas pela busca.
"""


def parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("resposta sem JSON")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON raiz inválido")
    return data


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.referer,
        "X-Title": "Radar Municipal Ribeirao",
    }


def web_plugins() -> list[dict]:
    return [
        {
            "id": "web",
            "engine": "perplexity",
            "max_results": 5,
            "search_prompt": (
                "Busque fontes oficiais de Ribeirão Preto/SP sobre o serviço citado. "
                "Priorize ribeiraopreto.sp.gov.br, saerp, rpmobi, diário oficial e gov.br. "
                "Cite URL https."
            ),
        }
    ]


def chat_json(system: str, user: str, timeout: float = 55) -> dict:
    if not settings.openrouter_key:
        raise RuntimeError("OPENROUTER_API_KEY ausente")
    body = {
        "model": settings.fact_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "plugins": web_plugins(),
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=_headers(),
            json=body,
        )
        if response.is_error:
            detail = response.text[:300]
            raise RuntimeError(f"OpenRouter {response.status_code}: {detail}")
        payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    data = parse_json(str(content))
    # anexa citações do plugin quando vierem
    for item in payload.get("citations") or []:
        url = item if isinstance(item, str) else str((item or {}).get("url") or "")
        if url.startswith("https://"):
            sources = data.setdefault("sources", [])
            if not any(str(s.get("url")) == url for s in sources if isinstance(s, dict)):
                sources.append(
                    {
                        "title": url,
                        "url": url,
                        "kind": "imprensa",
                        "excerpt": "citação da busca web",
                    }
                )
    return data


def sanitize(payload: dict, kb_matches: list[dict]) -> dict:
    status = str(payload.get("status") or "").strip().lower()
    if status not in ALLOWED:
        status = "inconclusivo"
    sources = []
    seen = set()
    for item in list(payload.get("sources") or []) + kb_sources(kb_matches):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "title": str(item.get("title") or url)[:180],
                "url": url[:500],
                "kind": str(item.get("kind") or "oficial")[:24],
                "excerpt": str(item.get("excerpt") or item.get("note") or "")[:280],
            }
        )
    if status in NEEDS_SOURCE and not sources:
        status = "sem_evidencia"
    try:
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    priority = str(payload.get("response_priority") or "media").lower()
    if priority not in {"alta", "media", "baixa", "nenhuma"}:
        priority = "media"
    needs = bool(payload.get("needs_response"))
    if status in {"contradito", "distorcido", "exagerado", "nao_e_bem_assim"}:
        needs = True
        if priority == "nenhuma":
            priority = "alta"
    if status == "sustentado" and priority == "nenhuma":
        needs = False
    theme = re.sub(r"[^a-z_]", "", str(payload.get("theme") or "").lower())
    if theme not in {"saude", "agua", "transporte", "limpeza", "atendimento", "outro"}:
        theme = (kb_matches[0].get("theme") if kb_matches else "outro") or "outro"
    note = str(payload.get("note") or "").strip()
    quote_hint = str(payload.get("quote") or "")
    context = payload.get("broadcast") if isinstance(payload.get("broadcast"), dict) else {}
    if not note or "sugestão" in note.casefold() or "orientação" in note.casefold():
        note = _fallback_note(
            status,
            str(payload.get("summary") or ""),
            kb_matches,
            sources,
            quote=quote_hint,
            theme=theme,
            context=context,
        )
    if "NOTA À IMPRENSA" not in note.upper():
        note = _fallback_note(
            status,
            str(payload.get("summary") or ""),
            kb_matches,
            sources,
            quote=quote_hint,
            theme=theme,
            context=context,
        )
    note = _ensure_broadcast_block(note, context)
    note = note.replace("Contradito", "Não procede").replace("contradito", "não procede")
    return {
        "status": status,
        "label": STAMP_LABEL.get(status, status),
        "confidence": confidence,
        "summary": str(payload.get("summary") or "")[:400],
        "missing": str(payload.get("missing") or "")[:300],
        "needs_response": needs,
        "response_priority": priority,
        "note": note[:2800],
        "theme": theme,
        "sources": sources[:8],
        "vehicle": str(context.get("vehicle") or "")[:120],
        "place": str(context.get("place") or "")[:120],
        "said_at": str(context.get("said_at") or "")[:80],
        "program": str(context.get("program") or "")[:160],
    }


def _press_subject(status: str, theme: str, quote: str) -> str:
    theme_label = {
        "saude": "saúde",
        "agua": "abastecimento de água",
        "transporte": "transporte coletivo",
        "limpeza": "coleta seletiva",
        "atendimento": "atendimento municipal",
    }.get(theme, "serviço público")
    if status in {"contradito", "distorcido", "exagerado", "nao_e_bem_assim"}:
        return f"Esclarecimento sobre {theme_label}"
    if status == "sustentado":
        return f"Informação confirmada sobre {theme_label}"
    return f"Atualização sobre {theme_label}"


def normalize_broadcast(context: dict | None = None) -> dict:
    ctx = dict(context or {})
    vehicle = str(ctx.get("vehicle") or "Veículo local").strip() or "Veículo local"
    program = str(ctx.get("program") or ctx.get("space") or "Programa / espaço não informado").strip()
    place = str(ctx.get("place") or "Ribeirão Preto (SP)").strip() or "Ribeirão Preto (SP)"
    date = str(ctx.get("date") or "").strip()
    time_label = str(ctx.get("time") or "").strip()
    said_at = str(ctx.get("said_at") or "").strip()
    if not said_at and (date or time_label):
        said_at = " · ".join(part for part in (date, time_label) if part)
    if said_at and " · " in said_at and not date:
        parts = [p.strip() for p in said_at.split("·")]
        if parts:
            date = parts[0]
        if len(parts) > 1:
            time_label = parts[1]
    if not date:
        date = "data a confirmar"
    if not time_label:
        time_label = "horário a confirmar"
    return {
        "vehicle": vehicle[:120],
        "program": program[:160],
        "place": place[:120],
        "date": date[:40],
        "time": time_label[:40],
        "said_at": said_at[:80] or f"{date} · {time_label}",
    }


def broadcast_block(context: dict | None = None) -> str:
    ctx = normalize_broadcast(context)
    return (
        "Referência da veiculação:\n"
        f"Veículo: {ctx['vehicle']}\n"
        f"Programa/espaço: {ctx['program']}\n"
        f"Data: {ctx['date']}\n"
        f"Horário: {ctx['time']}\n"
        f"Local: {ctx['place']}"
    )


def _ensure_broadcast_block(note: str, context: dict | None = None) -> str:
    text = (note or "").strip()
    block = broadcast_block(context)
    if "referência da veiculação" in text.casefold():
        return text
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for index, line in enumerate(lines):
        out.append(line)
        if not inserted and line.casefold().startswith("assunto:"):
            out.append("")
            out.append(block)
            out.append("")
            inserted = True
            # avoid double blank if next line already empty
            if index + 1 < len(lines) and not lines[index + 1].strip():
                continue
    if not inserted:
        out = [block, "", *out]
    return "\n".join(out).strip()


def _fallback_note(
    status: str,
    summary: str,
    kb_matches: list[dict],
    sources: list[dict],
    quote: str = "",
    theme: str = "",
    context: dict | None = None,
) -> str:
    detail = ""
    if kb_matches:
        detail = str(kb_matches[0].get("detail") or kb_matches[0].get("claim") or "")
    source_line = "Fonte oficial: Portal da Prefeitura de Ribeirão Preto"
    if sources:
        source_line = f"Fonte oficial: {sources[0]['title']} — {sources[0]['url']}"
    subject = _press_subject(status, theme or (kb_matches[0].get("theme") if kb_matches else ""), quote)
    public = STAMP_LABEL.get(status, status)
    if status in {"contradito", "distorcido", "exagerado", "nao_e_bem_assim"}:
        body = (
            f"Em atenção a informações veiculadas sobre o serviço público municipal, "
            f"a Prefeitura de Ribeirão Preto presta o seguinte esclarecimento.\n\n"
            f"Foi dito que “{quote}”. Classificação da assessoria: {public}. "
            f"{summary} {detail}\n\n"
            f"A Administração Municipal reforça os canais oficiais de atendimento e permanece "
            f"à disposição da imprensa e da população para esclarecimentos adicionais."
        )
    elif status == "sustentado":
        body = (
            f"A Prefeitura de Ribeirão Preto confirma informação divulgada sobre serviço municipal.\n\n"
            f"Conforme registrado em fonte oficial: {detail or summary}\n\n"
            f"A Assessoria de Comunicação disponibiliza o link oficial para consulta."
        )
    else:
        body = (
            f"A Prefeitura de Ribeirão Preto acompanha a menção feita em veículo local e "
            f"informa que segue apurando os dados disponíveis.\n\n"
            f"{summary} {detail}"
        )
    return (
        "NOTA À IMPRENSA — Prefeitura de Ribeirão Preto\n"
        f"Assunto: {subject}\n\n"
        f"{broadcast_block(context)}\n\n"
        f"{body.strip()}\n\n"
        f"{source_line}\n"
        "Assessoria de Comunicação / Prefeitura Municipal de Ribeirão Preto"
    )


def offline_verdict(
    quote: str,
    normalized: str,
    kb_ids: list[str] | None = None,
    context: dict | None = None,
) -> dict:
    """Caminho apresentável sem OpenRouter: KB oficial + regras."""
    matches = search_kb(f"{quote} {normalized}", hint_ids=kb_ids or [])
    sources = kb_sources(matches)
    folded = f"{quote} {normalized}".casefold()
    deny = any(
        mark in folded
        for mark in (
            "não tem",
            "nao tem",
            "não existe",
            "nao existe",
            "abandonou",
            "sem canal",
            "sem telefone",
            "no escuro",
        )
    )
    confirm = any(
        mark in folded
        for mark in ("uma vez por semana", "acontece", "funciona", "oferece")
    )

    if matches and deny:
        status = "contradito"
        needs = True
        priority = "alta"
        summary = "A fala não procede: a base oficial da Prefeitura documenta o serviço com URL."
    elif matches and confirm:
        status = "sustentado"
        needs = True
        priority = "baixa"
        summary = "A fala está correta perante o que o portal oficial registra."
    elif matches:
        status = "nao_e_bem_assim"
        needs = True
        priority = "media"
        summary = "Há dado oficial relacionado, mas o recorte da fala é impreciso."
    else:
        status = "sem_evidencia"
        needs = True
        priority = "media"
        summary = "Sem fato suficiente na base local; falta confirmação em fonte https."

    theme = matches[0]["theme"] if matches else "outro"
    broadcast = normalize_broadcast(context)
    payload = {
        "status": status,
        "confidence": 0.82 if sources else 0.35,
        "summary": summary,
        "missing": "" if sources else "Ampliar busca em páginas oficiais.",
        "needs_response": needs,
        "response_priority": priority,
        "theme": theme,
        "quote": quote,
        "broadcast": broadcast,
        "note": _fallback_note(
            status, summary, matches, sources, quote=quote, theme=theme, context=broadcast
        ),
        "sources": sources,
    }
    return sanitize(payload, matches)


def verify_claim(
    quote: str,
    normalized: str,
    theme: str = "",
    kb_ids: list[str] | None = None,
    use_live_web: bool = True,
    context: dict | None = None,
) -> dict:
    broadcast = normalize_broadcast(context)
    matches = search_kb(f"{quote} {normalized} {theme}", hint_ids=kb_ids or [])
    pipeline = [
        {"step": "kb", "ok": True, "detail": f"{len(matches)} fato(s) na base municipal"},
    ]
    if use_live_web and settings.openrouter_key:
        kb_block = json.dumps(matches, ensure_ascii=False, indent=2)
        user = (
            f"Frase ouvida:\n{quote}\n\nAfirmação normalizada:\n{normalized}\n\n"
            f"Tema sugerido: {theme or 'outro'}\n\n"
            f"Referência da veiculação (obrigatório na nota):\n"
            f"{json.dumps(broadcast, ensure_ascii=False, indent=2)}\n\n"
            f"Base de conhecimento da Prefeitura (use e cite as URLs):\n{kb_block}\n\n"
            "Faça busca web em fontes oficiais e devolva o JSON pedido."
        )
        try:
            raw = chat_json(CRISIS_SYSTEM, user)
            raw["quote"] = quote
            raw["broadcast"] = broadcast
            pipeline.append({"step": "web", "ok": True, "detail": "busca OpenRouter + plugin web"})
            verdict = sanitize(raw, matches)
            verdict["pipeline"] = pipeline
            verdict["mode"] = "live"
            return verdict
        except Exception as exc:
            pipeline.append({"step": "web", "ok": False, "detail": str(exc)[:180]})
    else:
        pipeline.append(
            {
                "step": "web",
                "ok": False,
                "detail": "modo base oficial (sem chave OpenRouter neste ambiente)",
            }
        )
    verdict = offline_verdict(quote, normalized, kb_ids=kb_ids, context=broadcast)
    verdict["pipeline"] = pipeline
    verdict["mode"] = "kb"
    return verdict
