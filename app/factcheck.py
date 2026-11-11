"""Checagem municipal + nota de crise sóbria."""

from __future__ import annotations

import json
import re

import httpx

from app.kb import kb_sources, search_kb
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

STAMP_LABEL = {
    "sustentado": "Confirmado",
    "sustentado_ressalva": "Confirmado, com ressalva",
    "nao_e_bem_assim": "Não é bem assim",
    "exagerado": "Exagerado",
    "distorcido": "Distorcido",
    "contradito": "Contradito",
    "sem_prova": "Sem prova",
    "sem_prova_publica": "Sem prova pública",
    "versoes_em_disputa": "Versões em disputa",
    "indicio": "Indício",
    "dados_incompletos": "Dados incompletos",
    "audio_confuso": "Áudio confuso",
    "sem_evidencia": "Sem evidência",
    "inconclusivo": "Inconclusivo",
    "promessa": "Promessa",
}

CRISIS_SYSTEM = """Você é especialista em comunicação de crise e assessoria de imprensa do poder público municipal brasileiro.
Cidade: Ribeirão Preto (SP).
Tom: sóbrio, factual, sem ataque, sem marketing eleitoral, sem a palavra fake.
Responda SÓ JSON:
{
  "status":"sustentado|sustentado_ressalva|nao_e_bem_assim|exagerado|distorcido|contradito|sem_prova|sem_prova_publica|versoes_em_disputa|indicio|dados_incompletos|audio_confuso|promessa",
  "confidence":0.0,
  "summary":"até 280 caracteres com o juízo factual",
  "missing":"o que falta para cravar, ou vazio",
  "needs_response":true,
  "response_priority":"alta|media|baixa|nenhuma",
  "note":"nota curta para a assessoria, 4 a 7 frases, pronta para adaptar. Cite o que a fonte oficial mostra. Não invente número nem URL.",
  "theme":"saude|agua|transporte|limpeza|atendimento|outro",
  "sources":[{"title":"...","url":"https://...","kind":"oficial|checagem|imprensa","excerpt":"trecho curto"}]
}

Regras:
- Carimbo fechado só com URL https nas sources OU com fato da base oficial já listado abaixo (aí repita a URL da base).
- Se a fala contradiz a base oficial com URL, use contradito ou distorcido.
- Se a fala confirma a base, use sustentado.
- needs_response=true quando o conteúdo exige posicionamento público (falso, parcial, distorcido, ou verdadeiro com risco de ruído).
- A nota não discute adversário político; responde o fato.
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
    if not note:
        note = _fallback_note(status, str(payload.get("summary") or ""), kb_matches, sources)
    return {
        "status": status,
        "label": STAMP_LABEL.get(status, status),
        "confidence": confidence,
        "summary": str(payload.get("summary") or "")[:400],
        "missing": str(payload.get("missing") or "")[:300],
        "needs_response": needs,
        "response_priority": priority,
        "note": note[:1600],
        "theme": theme,
        "sources": sources[:8],
    }


def _fallback_note(status: str, summary: str, kb_matches: list[dict], sources: list[dict]) -> str:
    label = STAMP_LABEL.get(status, status)
    bits = [
        f"Carimbo sugerido: {label}.",
        summary or "Segue leitura factual para a assessoria.",
    ]
    if kb_matches:
        bits.append(kb_matches[0].get("detail") or kb_matches[0].get("claim") or "")
    if sources:
        bits.append(f"Fonte principal: {sources[0]['title']} — {sources[0]['url']}")
    bits.append(
        "Sugestão: responder com o dado oficial, sem polemizar o veículo, e oferecer canal de atendimento ao munícipe."
    )
    return " ".join(bit for bit in bits if bit)


def offline_verdict(quote: str, normalized: str, kb_ids: list[str] | None = None) -> dict:
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
        summary = "A fala nega serviço que a base oficial da Prefeitura documenta com URL."
    elif matches and confirm:
        status = "sustentado"
        needs = True
        priority = "baixa"
        summary = "A fala está alinhada ao que o portal oficial registra."
    elif matches:
        status = "nao_e_bem_assim"
        needs = True
        priority = "media"
        summary = "Há dado oficial relacionado, mas o recorte da fala precisa de correção."
    else:
        status = "sem_evidencia"
        needs = True
        priority = "media"
        summary = "Sem fato suficiente na base local; falta confirmação em fonte https."

    payload = {
        "status": status,
        "confidence": 0.82 if sources else 0.35,
        "summary": summary,
        "missing": "" if sources else "Ampliar busca em páginas oficiais.",
        "needs_response": needs,
        "response_priority": priority,
        "theme": matches[0]["theme"] if matches else "outro",
        "note": "",
        "sources": sources,
    }
    if matches:
        detail = matches[0].get("detail") or ""
        payload["note"] = (
            f"A imprensa/ouvinte afirmou: “{quote}”. "
            f"Leitura: {summary} "
            f"Dado oficial: {detail} "
            f"Posicionamento sugerido: informar o canal e o horário corretos, "
            f"oferecer o link da página oficial e, se couber, atendimento ao caso concreto do munícipe. "
            f"Tom sóbrio, sem desqualificar o veículo."
        )
    return sanitize(payload, matches)


def verify_claim(
    quote: str,
    normalized: str,
    theme: str = "",
    kb_ids: list[str] | None = None,
    use_live_web: bool = True,
) -> dict:
    matches = search_kb(f"{quote} {normalized} {theme}", hint_ids=kb_ids or [])
    pipeline = [
        {"step": "kb", "ok": True, "detail": f"{len(matches)} fato(s) na base municipal"},
    ]
    if use_live_web and settings.openrouter_key:
        kb_block = json.dumps(matches, ensure_ascii=False, indent=2)
        user = (
            f"Frase ouvida:\n{quote}\n\nAfirmação normalizada:\n{normalized}\n\n"
            f"Tema sugerido: {theme or 'outro'}\n\n"
            f"Base de conhecimento da Prefeitura (use e cite as URLs):\n{kb_block}\n\n"
            "Faça busca web em fontes oficiais e devolva o JSON pedido."
        )
        try:
            raw = chat_json(CRISIS_SYSTEM, user)
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
    verdict = offline_verdict(quote, normalized, kb_ids=kb_ids)
    verdict["pipeline"] = pipeline
    verdict["mode"] = "kb"
    return verdict
