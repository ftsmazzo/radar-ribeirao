"""Monitoramento de portais locais via RSS."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import unescape

import httpx

from app.settings import ROOT, settings

_CACHE_LOCK = threading.Lock()
_CACHE: dict = {
    "fetched_at": "",
    "items": [],
    "sources": [],
    "errors": [],
}
_CACHE_TTL = 180  # segundos


def _fold(text: str) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(raw.casefold().split())


@lru_cache(maxsize=1)
def load_portals_config() -> dict:
    path = ROOT / "data" / "portals.json"
    return json.loads(path.read_text(encoding="utf-8"))


# Cidade explícita no texto (manchete de rede nacional / regional ampla).
STRONG_CITY_MARKERS = (
    "ribeirao preto",
    "ribeirao e franca",
    "ribeirao preto e franca",
    "bonfim paulista",
)

NEIGHBORHOOD_MARKERS = (
    "campos eliseos",
    "jardim paulista",
    "cidade nova",
    "vila virginia",
    "vila tibério",
    "vila tiberio",
    "ribeirao verde",
    "sumarezinho",
    "ipiranga",
    "centro de ribeirao",
)

LOCAL_ORG_MARKERS = (
    "saerp",
    "rp mobi",
    "rpmobi",
    "ribeirao mobilidade",
    "prefeitura de ribeirao",
    "prefeitura municipal de ribeirao",
    "camara de ribeirao",
    "camara municipal de ribeirao",
    "ubds ",
)

# Serviço público municipal — evita "prefeitura" solta de outra cidade.
SERVICE_MARKERS = (
    "saerp",
    "rp mobi",
    "rpmobi",
    "ribeirao mobilidade",
    "secretaria da saude",
    "secretaria de saude",
    "secretaria municipal",
    "secretaria de obras",
    "secretaria de educacao",
    "secretaria de educação",
    "ubs ",
    "upa ",
    "ubds ",
    "posto de saude",
    "pronto atendimento",
    "pronto-socorro",
    "poupatempo",
    "iptu",
    "coleta seletiva",
    "ecoponto",
    "falta dagua",
    "falta d agua",
    "falta de agua",
    "racionamento de agua",
    "vazamento",
    "onibus urbano",
    "transporte coletivo",
    "ponto de onibus",
    "parada de onibus",
    "camara municipal",
    "asfalto",
    "tapa buraco",
    "tapa-buraco",
    "iluminacao publica",
    "vacinacao",
    "campanha de vacina",
    "zerar o dengue",
    "combate a dengue",
    "combate à dengue",
    "guarda municipal",
    "guarda civil",
    "gcrm",
    "servico funerario",
    "serviço funerário",
    "limpeza urbana",
    "varrição",
    "varricao",
)

# Em feed 100% local, "prefeitura" sem outra cidade costuma ser a daqui.
LOCAL_FEED_SERVICE = (
    "prefeitura",
    "camara municipal",
    "câmara municipal",
    "secretario municipal",
    "secretário municipal",
    "secretaria municipal",
)

NOISE_MARKERS = (
    "futebol",
    "brasileirao",
    "campeonato brasileiro",
    "serie a",
    "série a",
    "serie b",
    "libertadores",
    "copa do brasil",
    "gol ",
    " gols",
    "placar",
    "classicos",
    "clássico",
    "botafogo",
    "corinthians",
    "palmeiras",
    "sao paulo fc",
    "são paulo fc",
    "santos fc",
    "flamengo",
    "horoscopo",
    "horóscopo",
    "novela",
    "bbb",
    "celebridade",
    "receita de",
    "reality",
    "bastidores da tv",
    "shows e festas",
    "agenda cultural",
    "mega-sena",
    "megasena",
    "lotofacil",
    "loteria",
    "signos",
    "astrologia",
    "cinema",
    "estreia de filme",
)


def _has_any(blob: str, markers: tuple[str, ...]) -> bool:
    return any(mark in blob for mark in markers)


def classify(title: str, summary: str, *, local_feed: bool = False) -> dict:
    """Classifica manchete para a fila da assessoria.

    Só entra em Prefeitura com órgão/serviço municipal explícito.
    Feed local NÃO basta: acidente, feira e polícia ficam em Geral/Cidade.
    """
    blob = _fold(f"{title} {summary}")
    strong_city = _has_any(blob, STRONG_CITY_MARKERS)
    neighborhood = _has_any(blob, NEIGHBORHOOD_MARKERS)
    local_org = _has_any(blob, LOCAL_ORG_MARKERS)
    service = (
        local_org
        or _has_any(blob, SERVICE_MARKERS)
        or (local_feed and _has_any(blob, LOCAL_FEED_SERVICE))
        or (strong_city and "prefeitura" in blob)
    )
    noise = _has_any(blob, NOISE_MARKERS)
    # Contexto geográfico: feed local ajuda serviço, mas sozinho não vira fila.
    municipal_context = local_feed or strong_city or neighborhood

    if noise and not local_org and not (service and municipal_context):
        tag = "ruido"
        label = "Fora do escopo"
        priority = "baixa"
    elif local_org or (service and municipal_context):
        tag = "prefeitura"
        label = "Prefeitura / serviço"
        priority = "alta"
    elif strong_city or neighborhood:
        # Cidade citada, sem serviço municipal — fora da fila padrão.
        tag = "cidade"
        label = "Cidade (sem serviço)"
        priority = "baixa"
    elif local_feed:
        tag = "geral"
        label = "Cobertura local"
        priority = "baixa"
    else:
        tag = "geral"
        label = "Geral / região"
        priority = "baixa"

    return {
        "tag": tag,
        "label": label,
        "priority": priority,
        "city_hit": bool(strong_city or neighborhood or local_feed),
        "service_hit": bool(service),
        "checkable": tag == "prefeitura",
        "reasons": _reasons(strong_city, neighborhood, local_feed, local_org, service, noise),
    }


def _reasons(
    strong_city: bool,
    neighborhood: bool,
    local_feed: bool,
    local_org: bool,
    service: bool,
    noise: bool,
) -> list[str]:
    out = []
    if strong_city:
        out.append("cidade")
    if neighborhood:
        out.append("bairro")
    if local_feed:
        out.append("feed local")
    if local_org:
        out.append("órgão local")
    if service:
        out.append("serviço")
    if noise:
        out.append("ruído")
    return out


def _strip_html(raw: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    return " ".join(text.split())


def _parse_date(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return text[:40]


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        if _local(child.tag) in names:
            return (child.text or "").strip() or _strip_html("".join(child.itertext()))
    return ""


def parse_feed(xml_text: str, portal: dict) -> list[dict]:
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    nodes = []
    for node in root.iter():
        if _local(node.tag) in {"item", "entry"}:
            nodes.append(node)
    for node in nodes[:40]:
        title = _strip_html(_child_text(node, ("title",)))
        link = _child_text(node, ("link",))
        if not link:
            for child in list(node):
                if _local(child.tag) == "link":
                    link = (child.attrib.get("href") or "").strip()
                    if link:
                        break
        summary = _strip_html(
            _child_text(node, ("description", "summary", "content", "encoded"))
        )
        published = _parse_date(
            _child_text(node, ("pubDate", "published", "updated", "date"))
        )
        if not title or not link:
            continue
        score = classify(title, summary, local_feed=True)
        item_id = hashlib.sha1(f"{portal['id']}|{link}".encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "id": item_id,
                "portal_id": portal["id"],
                "portal_name": portal["name"],
                "portal_logo": portal.get("logo") or "",
                "portal_site": portal.get("site") or "",
                "group": portal.get("group") or "",
                "title": title[:240],
                "summary": summary[:420],
                "url": link[:500],
                "published_at": published,
                **score,
            }
        )
    return items


def _fetch_one(portal: dict) -> tuple[list[dict], str]:
    feed = (portal.get("feed") or "").strip()
    if not feed:
        return [], portal.get("note") or "sem feed"
    headers = {
        "User-Agent": "RadarMunicipal/1.0 (+https://inteligenciaeleitoral.com.br)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    try:
        with httpx.Client(timeout=18.0, follow_redirects=True) as client:
            response = client.get(feed, headers=headers)
            response.raise_for_status()
            items = parse_feed(response.text, portal)
            return items, ""
    except Exception as exc:
        return [], str(exc)[:160]


def refresh_portals(force: bool = False) -> dict:
    now = time.time()
    with _CACHE_LOCK:
        fetched_at = _CACHE.get("fetched_at") or ""
        age_ok = False
        if fetched_at and not force:
            try:
                age_ok = (now - datetime.fromisoformat(fetched_at).timestamp()) < _CACHE_TTL
            except Exception:
                age_ok = False
        if age_ok:
            return dict(_CACHE)

    config = load_portals_config()
    portals = config.get("portals") or []
    items: list[dict] = []
    errors: list[dict] = []
    sources = []

    for portal in portals:
        sources.append(
            {
                "id": portal["id"],
                "name": portal["name"],
                "group": portal.get("group") or "",
                "site": portal.get("site") or "",
                "logo": portal.get("logo") or "",
                "enabled": bool(portal.get("enabled")),
                "note": portal.get("note") or "",
                "has_feed": bool(portal.get("feed")),
            }
        )
        if not portal.get("enabled"):
            continue
        found, err = _fetch_one(portal)
        if err:
            errors.append({"portal_id": portal["id"], "name": portal["name"], "error": err})
        items.extend(found)

    # Relevância primeiro; dentro do grupo, mais novo em cima.
    rank = {"prefeitura": 0, "cidade": 1, "geral": 2, "ruido": 3}
    items.sort(
        key=lambda i: (
            rank.get(i.get("tag"), 9),
            -(
                datetime.fromisoformat(i["published_at"]).timestamp()
                if i.get("published_at") and "T" in i["published_at"]
                else 0
            ),
        )
    )

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "city": settings.city_name,
        "items": items[:120],
        "sources": sources,
        "errors": errors,
        "counts": {
            "total": len(items),
            "prefeitura": sum(1 for i in items if i.get("tag") == "prefeitura"),
            "cidade": sum(1 for i in items if i.get("tag") == "cidade"),
            "geral": sum(1 for i in items if i.get("tag") == "geral"),
            "ruido": sum(1 for i in items if i.get("tag") == "ruido"),
            "relevantes": sum(1 for i in items if i.get("tag") == "prefeitura"),
            "fila": sum(1 for i in items if i.get("checkable")),
            "ativos": sum(1 for s in sources if s.get("enabled")),
        },
    }
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE.update(payload)
        return dict(_CACHE)


def portals_snapshot(force: bool = False, tag: str = "") -> dict:
    data = refresh_portals(force=force)
    items = list(data.get("items") or [])
    # Padrão: só prefeitura/serviço — não “tudo de Ribeirão”.
    wanted = (tag or "").strip().lower() or "prefeitura"
    if wanted in {"relevantes", "fila"}:
        wanted = "prefeitura"
    if wanted in {"prefeitura", "cidade", "geral", "ruido"}:
        items = [item for item in items if item.get("tag") == wanted]
    elif wanted in {"todos", "all"}:
        wanted = "todos"
    else:
        wanted = "prefeitura"
        items = [item for item in items if item.get("tag") == "prefeitura"]
    out = dict(data)
    out["items"] = items
    out["filter"] = wanted
    return out
