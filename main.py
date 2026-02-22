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
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    messages = [{"role": m.role, "content": m.content} for m in request.history]
    messages.append({"role": "user", "content": request.message})

    # Keep history to last 10 exchanges
    if len(messages) > 20:
        messages = messages[-20:]

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    return {"response": response.content[0].text}


@app.get("/health")
async def health():
    return {"status": "ok"}
