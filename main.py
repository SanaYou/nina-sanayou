from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import anthropic
import os
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


def load_knowledge() -> str:
    knowledge_dir = Path("knowledge")
    parts = []

    for file in sorted(knowledge_dir.glob("*.md")):
        parts.append(file.read_text(encoding="utf-8"))

    articles_dir = knowledge_dir / "articles"
    if articles_dir.exists():
        for file in sorted(articles_dir.glob("*.md")):
            parts.append(file.read_text(encoding="utf-8"))

    instructies_dir = knowledge_dir / "instructies"
    if instructies_dir.exists():
        for file in sorted(instructies_dir.glob("*.md")):
            parts.append(file.read_text(encoding="utf-8"))

    return "\n\n---\n\n".join(parts)


KNOWLEDGE = load_knowledge()

SYSTEM_PROMPT = f"""Je bent Nina, de digitale assistent van SanaYou YOGAcademy.

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

## Kennisbank

{KNOWLEDGE}"""


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

        messages = [{"role": m.role, "content": m.content} for m in request.history]
        messages.append({"role": "user", "content": request.message})

        # Keep history to last 10 exchanges
        if len(messages) > 20:
            messages = messages[-20:]

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        return {"response": response.content[0].text}
    except anthropic.AuthenticationError:
        return {"error": "Er is een configuratieprobleem. Neem contact op met academy@sanayou.com."}
    except Exception as e:
        print(f"[chat] error: {type(e).__name__}: {e}")
        return {"error": "Er ging iets mis. Probeer het opnieuw of mail naar academy@sanayou.com."}


@app.get("/api/articles")
async def get_articles():
    articles = []
    articles_dir = Path("knowledge/articles")
    if not articles_dir.exists():
        return []

    for file in sorted(articles_dir.glob("*.md")):
        content = file.read_text(encoding="utf-8")
        title = _extract_title(content, file.stem)
        collection = _extract_collection(content)
        articles.append({
            "id": file.stem,
            "title": title,
            "collection": collection,
            "content": content,
        })

    return articles


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        # Intercom format: TITEL: ...
        if line.startswith("TITEL:"):
            return line.replace("TITEL:", "").strip()
        # Markdown H1
        if line.startswith("# "):
            title = line.lstrip("# ").strip()
            # Strip "Helpartikel — " prefix if present
            if title.startswith("Helpartikel"):
                title = title.split("—", 1)[-1].strip()
            return title
    return fallback.replace("-", " ").title()


def _extract_collection(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("COLLECTIE:"):
            return line.replace("COLLECTIE:", "").strip()
    # Fallback: try to detect from content
    text_lower = content.lower()
    if "ryt300" in text_lower:
        return "RYT300 Teacher Training"
    if "ryt/vyn200" in text_lower or "klassikaal" in text_lower:
        return "RYT/VYN200 Klassikaal"
    if "online" in text_lower and "ryt200" in text_lower:
        return "RYT200 Online"
    if "betaling" in text_lower or "factuur" in text_lower or "termijn" in text_lower:
        return "Betaling & Facturatie"
    if "examen" in text_lower or "herkansing" in text_lower:
        return "Examens & Herkansingen"
    if "huddle" in text_lower or "inloggen" in text_lower or "technisch" in text_lower:
        return "Technische Ondersteuning"
    return "Algemeen"


@app.get("/health")
async def health():
    return {"status": "ok"}
