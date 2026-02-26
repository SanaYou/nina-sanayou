from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import anthropic
import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Artikel index (geladen bij startup) ────────────────────────────────────
def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("TITEL:"):
            return line.replace("TITEL:", "").strip()
        if line.startswith("# "):
            title = line.lstrip("# ").strip()
            for sep in [" – ", " — "]:
                if sep in title:
                    title = title.split(sep, 1)[1].strip()
                    break
            return title
    return fallback.replace("-", " ").title()


def _extract_collection(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("COLLECTIE:"):
            return line.replace("COLLECTIE:", "").strip()
    return "Algemeen"


def _extract_tags(content: str) -> list:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("TAGS:"):
            raw = line.replace("TAGS:", "").strip()
            return [t.strip().lower() for t in raw.split(",")]
    return []


def load_articles_index() -> list:
    """Laad alle artikelen als geïndexeerde documenten voor RAG."""
    articles_dir = Path("knowledge/articles")
    if not articles_dir.exists():
        return []
    index = []
    for file in sorted(articles_dir.glob("*.md")):
        content = file.read_text(encoding="utf-8")
        title = _extract_title(content, file.stem)
        collection = _extract_collection(content)
        tags = _extract_tags(content)
        # Strip metadata lines voor schone content in de prompt
        clean_lines = [
            l for l in content.splitlines()
            if not l.startswith("COLLECTIE:") and not l.startswith("TAGS:")
        ]
        clean_content = "\n".join(clean_lines).strip()
        index.append({
            "id": file.stem,
            "title": title,
            "collection": collection,
            "tags": tags,
            "content": content,          # voor helpcenter API
            "clean_content": clean_content,  # voor Nina's prompt
        })
    return index


ARTICLES_INDEX = load_articles_index()

# Laad ook kleine kennisbestanden in root (bijv. anchorlinks.md)
def load_base_knowledge() -> str:
    knowledge_dir = Path("knowledge")
    parts = []
    # Laad root-bestanden (bijv. anchorlinks.md)
    for file in sorted(knowledge_dir.glob("*.md")):
        parts.append(file.read_text(encoding="utf-8"))
    # Laad instructies voor Nina (gedragsregels, doorvragen, etc.)
    instructies_dir = knowledge_dir / "instructies"
    if instructies_dir.exists():
        for file in sorted(instructies_dir.glob("*.md")):
            parts.append(file.read_text(encoding="utf-8"))
    return "\n\n".join(parts)

BASE_KNOWLEDGE = load_base_knowledge()


# ── RAG: haal relevante artikelen op ───────────────────────────────────────
def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower())


def retrieve_articles(query: str, history: List, top_k: int = 6) -> str:
    """Selecteer de meest relevante artikelen op basis van de vraag + recent gesprek."""
    # Combineer huidige vraag + laatste 3 berichten voor context
    search_text = query
    for msg in history[-6:]:
        search_text += " " + msg.content
    words = [w for w in normalize(search_text).split() if len(w) > 2]

    if not words:
        return ""

    scored = []
    for article in ARTICLES_INDEX:
        score = 0
        title_norm = normalize(article["title"])
        tags_norm = normalize(" ".join(article["tags"]))
        content_norm = normalize(article["clean_content"])

        for word in words:
            if word in title_norm:
                score += 8
            if word in tags_norm:
                score += 4
            if word in content_norm:
                score += 1

        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    relevant = [a for _, a in scored[:top_k]]

    if not relevant:
        return ""

    parts = []
    for a in relevant:
        parts.append(f"## {a['title']}\n(Collectie: {a['collection']})\n\n{a['clean_content']}")
    return "\n\n---\n\n".join(parts)


# ── System prompt (zonder kennisbank — die wordt dynamisch ingevoegd) ──────
SYSTEM_PROMPT_BASE = """Je bent Nina, de digitale assistent van SanaYou YOGAcademy.

Je helpt studenten en geïnteresseerden met vragen over opleidingen, prijzen, planning, technische ondersteuning en meer. Je communiceert warm, professioneel en to-the-point — in de stem van Sandy Karsten, hoofd docentenopleider van SanaYou YOGAcademy.

## Toon en schrijfstijl

- Warm en persoonlijk — de bezoeker voelt zich gezien en welkom
- Deskundig maar toegankelijk — geen vakjargon tenzij de ander dat zelf gebruikt
- Beknopt maar volledig — geen opvulzinnen, maar ook geen informatie weglaten
- Gebruik altijd "je" (nooit "u")
- Geen "Geachte" of andere stijve formuleringen
- Geen overdreven enthousiasme of salesachtige taal
- Sluit antwoorden waar passend af met een open deur: "Heb je nog vragen, dan help ik je graag verder."

## Regels

1. Antwoord altijd in het Nederlands
2. Gebruik uitsluitend informatie uit de kennisbank hieronder — verzin niets
3. Als informatie ontbreekt of onduidelijk is, zeg dat eerlijk en geef aan dat de bezoeker kan mailen naar academy@sanayou.com
4. Bij klachten, gevoelige situaties of uitzonderingen: geef aan dat Sandy persoonlijk contact opneemt en vraag om te mailen naar academy@sanayou.com
5. Voeg altijd de relevante link toe als die in de kennisbank staat
6. Houd antwoorden overzichtelijk — gebruik korte alinea's of tussenkopjes bij langere antwoorden
7. Schrijf nooit in opsommingen tenzij het echt een stapsgewijze instructie of een lijst van opties is

## Vragen stellen

- Stel nooit meer dan één vervolgvraag per bericht
- Kies de meest bepalende vraag als je meer informatie nodig hebt
- Gooi nooit meerdere vragen tegelijk op de bezoeker

## Wanneer wel en niet de Calendly-link geven

Geef de link naar het gratis studieadviesgesprek (https://calendly.com/sanayou-sandy/studie-advies-gesprek) ALLEEN in deze situaties:
- De bezoeker twijfelt expliciet welke opleiding bij hen past na een uitgebreid gesprek
- De persoonlijke situatie is te complex om via chat goed te begeleiden (bijv. eerder behaalde certificaten, gezondheid, maatwerk)
- De bezoeker vraagt zelf om een gesprek

Geef de Calendly-link NIET:
- Als standaard afsluiting van een antwoord
- Als de vraag informatief is en je die gewoon kunt beantwoorden
- Bij technische vragen of procedurevragen

## Anchorlinks en URL's

{BASE_KNOWLEDGE}

## Kennisbank (relevante artikelen voor deze vraag)

{{ARTICLES}}"""

SYSTEM_PROMPT_BASE = SYSTEM_PROMPT_BASE.replace("{BASE_KNOWLEDGE}", BASE_KNOWLEDGE)


# ── Chat endpoint ───────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Message]] = []


@app.post("/chat")
async def chat(request: ChatRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "API key niet geconfigureerd. Neem contact op met academy@sanayou.com."}

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # RAG: haal relevante artikelen op voor deze vraag
        relevant_knowledge = retrieve_articles(request.message, request.history or [])
        system_prompt = SYSTEM_PROMPT_BASE.replace("{ARTICLES}", relevant_knowledge or "(geen specifieke artikelen gevonden)")

        messages = [{"role": m.role, "content": m.content} for m in (request.history or [])]
        messages.append({"role": "user", "content": request.message})

        # Maximaal 20 berichten geschiedenis
        if len(messages) > 20:
            messages = messages[-20:]

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )

        return {"response": response.content[0].text}
    except anthropic.AuthenticationError:
        return {"error": "Er is een configuratieprobleem. Neem contact op met academy@sanayou.com."}
    except Exception as e:
        import traceback
        print(f"[chat] error: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        return {"error": "Er ging iets mis. Probeer het opnieuw of mail naar academy@sanayou.com."}


# ── Helpcenter API ──────────────────────────────────────────────────────────
@app.get("/api/articles")
async def get_articles():
    articles_dir = Path("knowledge/articles")
    if not articles_dir.exists():
        return []

    volgorde_path = Path("knowledge/volgorde.json")
    volgorde = {}
    if volgorde_path.exists():
        import json as _json
        raw = _json.loads(volgorde_path.read_text(encoding="utf-8"))
        for col, items in raw.items():
            for i, item in enumerate(items):
                volgorde[item["bestand"]] = (col, i)

    articles = []
    for file in sorted(articles_dir.glob("*.md")):
        content = file.read_text(encoding="utf-8")
        title = _extract_title(content, file.stem)
        collection = _extract_collection(content)
        tags = _extract_tags(content)
        order = volgorde.get(file.name, (collection, 9999))[1]
        articles.append({
            "id": file.stem,
            "title": title,
            "collection": collection,
            "tags": tags,
            "content": content,
            "order": order,
        })

    articles.sort(key=lambda a: (a["collection"], a["order"]))
    return articles


@app.get("/health")
async def health():
    return {"status": "ok"}
