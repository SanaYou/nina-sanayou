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

import logging

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nina")

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

# ── Startup-validatie: check artikelen op ontbrekende metadata ────────────────
def validate_articles(index: list):
    """Log waarschuwingen voor artikelen zonder COLLECTIE of TAGS."""
    missing_collectie = []
    missing_tags = []
    for article in index:
        if article["collection"] == "Algemeen":
            # Check of er echt geen COLLECTIE: regel was (niet bewust "Algemeen")
            raw = Path("knowledge/articles") / f"{article['id']}.md"
            if raw.exists():
                content = raw.read_text(encoding="utf-8")
                if "COLLECTIE:" not in content:
                    missing_collectie.append(article["id"])
        if not article["tags"]:
            missing_tags.append(article["id"])
    if missing_collectie:
        logger.warning(f"Artikelen ZONDER collectie ({len(missing_collectie)}): {', '.join(missing_collectie[:5])}{'...' if len(missing_collectie) > 5 else ''}")
    if missing_tags:
        logger.warning(f"Artikelen ZONDER tags ({len(missing_tags)}): {', '.join(missing_tags[:5])}{'...' if len(missing_tags) > 5 else ''}")
    logger.info(f"Kennisbank geladen: {len(index)} artikelen")

validate_articles(ARTICLES_INDEX)

# Laad ook kleine kennisbestanden in root (bijv. anchorlinks.md)
def load_base_knowledge() -> str:
    knowledge_dir = Path("knowledge")
    parts = []
    # Laad root-bestanden (anchorlinks, jaarkalender, etc.)
    for file in sorted(knowledge_dir.glob("*.md")):
        parts.append(file.read_text(encoding="utf-8"))
    # Laad alleen universele gedragsregels — content-specifieke instructies
    # worden via RAG geladen zodat ze alleen meekomen als ze relevant zijn.
    ALTIJD_LADEN = {
        "gesprek-afsluiten.md",
        "schrijfstijl.md",
        "doorvragen-voor-informatie.md",
        "past-bij-mij-vragen.md",
        "kortingen-kosten.md",
        "whatsapp-telefoonnummer.md",
        "naam-email-verzamelen.md",
    }
    instructies_dir = knowledge_dir / "instructies"
    if instructies_dir.exists():
        for file in sorted(instructies_dir.glob("*.md")):
            if file.name in ALTIJD_LADEN:
                parts.append(file.read_text(encoding="utf-8"))
    return "\n\n".join(parts)

BASE_KNOWLEDGE = load_base_knowledge()


# ── RAG: haal relevante artikelen op ───────────────────────────────────────
def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower())


# Synoniemenlijst: mensen gebruiken andere woorden dan in de kennisbank staan
SYNONIEMEN = {
    "kosten": ["prijs", "prijzen", "bedrag", "betalen", "investering", "tarief"],
    "prijs": ["kosten", "bedrag", "betalen", "investering", "tarief"],
    "betalen": ["kosten", "prijs", "betaling", "afrekenen"],
    "inschrijven": ["aanmelden", "registreren", "opgeven", "boeken"],
    "aanmelden": ["inschrijven", "registreren", "opgeven", "boeken"],
    "starten": ["beginnen", "start", "startdatum", "wanneer"],
    "beginnen": ["starten", "start", "startdatum", "wanneer"],
    "examen": ["toets", "beoordeling", "certificering", "examens"],
    "toets": ["examen", "beoordeling", "examens"],
    "certificaat": ["diploma", "certificering", "getuigschrift"],
    "diploma": ["certificaat", "certificering", "getuigschrift"],
    "rooster": ["planning", "lesrooster", "schema", "agenda", "lesdagen"],
    "planning": ["rooster", "lesrooster", "schema", "agenda", "lesdagen"],
    "online": ["afstandsleren", "digitaal", "thuisstudie"],
    "klassikaal": ["fysiek", "locatie", "zwolle", "studio"],
    "opzeggen": ["annuleren", "stoppen", "beeindigen", "uitschrijven"],
    "annuleren": ["opzeggen", "stoppen", "beeindigen", "uitschrijven"],
    "docent": ["leraar", "opleider", "trainer", "sandy"],
    "voorwaarden": ["regels", "eisen", "toelatingseisen", "vereisten"],
    "eisen": ["voorwaarden", "toelatingseisen", "vereisten"],
    "lesdag": ["lesmoment", "contactdag", "module", "bijeenkomst"],
    "module": ["lesdag", "onderdeel", "blok", "cursus"],
    "gemist": ["afwezig", "absent", "inhalen", "gemiste"],
    "inhalen": ["gemist", "herkansing", "missen"],
    "betaalregeling": ["termijnen", "gespreid", "gespreide betaling"],
    "termijnen": ["betaalregeling", "gespreid", "gespreide betaling"],
}


def expand_with_synonyms(words: list) -> list:
    """Breid zoekwoorden uit met synoniemen voor betere matching."""
    expanded = list(words)
    for word in words:
        if word in SYNONIEMEN:
            for syn in SYNONIEMEN[word]:
                if syn not in expanded:
                    expanded.append(syn)
    return expanded


def retrieve_articles(query: str, history: List, top_k: int = 3) -> str:
    """Selecteer de meest relevante artikelen op basis van de vraag + recent gesprek."""
    # Combineer huidige vraag + laatste 3 berichten voor context
    search_text = query
    for msg in history[-6:]:
        search_text += " " + msg.content
    base_words = [w for w in normalize(search_text).split() if len(w) > 2]
    words = expand_with_synonyms(base_words)

    if not words:
        return ""

    base_words_set = set(base_words)

    scored = []
    for article in ARTICLES_INDEX:
        score = 0
        title_norm = normalize(article["title"])
        tags_norm = normalize(" ".join(article["tags"]))
        content_norm = normalize(article["clean_content"])

        for word in words:
            # Synoniemen scoren de helft van directe matches
            multiplier = 1.0 if word in base_words_set else 0.5
            if word in title_norm:
                score += 8 * multiplier
            if word in tags_norm:
                score += 4 * multiplier
            if word in content_norm:
                score += 1 * multiplier

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

Je helpt studenten en geïnteresseerden met hun vragen. Je communiceert warm, professioneel en to-the-point — in de stem van Sandy Karsten, hoofddocent van SanaYou YOGAcademy. Houd je antwoorden kort en natuurlijk — nooit lange opsommingen van wat je allemaal kunt.

## Toon en schrijfstijl

- Warm en persoonlijk — de bezoeker voelt zich gezien en welkom
- Deskundig maar toegankelijk — geen vakjargon tenzij de ander dat zelf gebruikt
- Beknopt maar volledig — geen opvulzinnen, maar ook geen informatie weglaten. Geef bij producten/packs meteen de praktische info (wanneer, hoe vaak, wat als je niet kunt, kosten) — laat de klant er niet om hoeven vragen
- Gebruik altijd "je" (nooit "u")
- Geen "Geachte" of "Hey" — gebruik "Hoi" of soms "Hi"
- Nooit "Graag geholpen" of vergelijkbare afsluitende zinnen aan het begin van een gesprek — dat slaat nergens op, je hebt nog niets geholpen
- "Ik help je graag" mag wel, dat is een aanbod
- Geen overdreven enthousiasme of salesachtige taal
- Sluit antwoorden waar passend af met een open deur: "Heb je nog vragen, dan help ik je graag verder."
- Gebruik NOOIT een lange dash (—). Gebruik in plaats daarvan een komma, punt of dubbele punt.
- Schrijf "Welke spreekt je het meest aan?" — NOOIT "Welke aanspreekt je?" of varianten daarvan.

## Regels

1. Antwoord altijd in het Nederlands
2. Gebruik uitsluitend informatie uit de kennisbank hieronder — verzin niets
3. Als informatie ontbreekt of onduidelijk is, zeg dat eerlijk en bied aan om het uit te zoeken. Verwijs NOOIT naar een e-mailadres — jij bent het contactpunt.
4. Bij klachten, gevoelige situaties of uitzonderingen: geef aan dat Sandy persoonlijk contact opneemt en vraag om naam en e-mailadres zodat je het kunt doorsturen (via de escalatieprocedure)
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
- Als iemand AL in opleiding zit — studieadvies is alleen voor mensen die nog GEEN opleiding doen

## Studenten die willen oefenen of feedback willen vóór hun examen

Als iemand al in opleiding zit en wil oefenen, feedback wil op lesgeven, of zich wil voorbereiden op het examen, verwijs dan ALTIJD naar de Support Packs:
- **Teaching Assessment Pack** (€ 145) — lesvideo insturen + schriftelijk feedback + 60 min CoachCall met Sandy. Ideaal als examenvoorbereiding.
- **LIVE+ Support Pack** (€ 75/jaar) — maandelijkse CoachCall, community, gastdocent Q&A's. Voor doorlopende vragen en structuur.
Verwijs NOOIT naar studieadvies voor deze situaties.

## Anchorlinks en URL's

{BASE_KNOWLEDGE}

## Kennisbank (relevante artikelen voor deze vraag)

{ARTICLES}"""

SYSTEM_PROMPT_BASE = SYSTEM_PROMPT_BASE.replace("{BASE_KNOWLEDGE}", BASE_KNOWLEDGE)


# ── Chat endpoint ───────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str


TAALCHECK_PROMPT = """Je bent een Nederlandse taalredacteur. Je krijgt een chatbericht en controleert alleen of er letterlijke Engelse vertalingen in zitten die in het Nederlands raar klinken (calques).

Voorbeelden van fouten:
- "je weet waar je voeten staan" → "je hebt al een goede basis"
- "trek je yoga aan" → "spreekt yoga je aan"
- "dat maakt zin" → "dat heeft zin"
- "ik ben geëxciteerd" → "ik ben enthousiast"
- "neem uw tijd" → "neem de tijd"

Als je een fout vindt: geef alleen de gecorrigeerde tekst terug, geen uitleg.
Als de tekst al goed is: geef de tekst ongewijzigd terug.
Pas NIETS anders aan — geen stijl, geen inhoud, geen opmaak."""


def _taalcheck(client: anthropic.Anthropic, text: str) -> str:
    """Corrigeer Engelse calques in Nina's antwoord. Tijdslimiet 5s, bij fout origineel teruggeven."""
    try:
        check_client = anthropic.Anthropic(api_key=client.api_key, timeout=5.0)
        result = check_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": f"{TAALCHECK_PROMPT}\n\nTekst:\n{text}"}],
        )
        corrected = result.content[0].text.strip()
        if corrected and corrected != text:
            logger.info("Taalcheck: correctie toegepast")
        return corrected if corrected else text
    except Exception as e:
        logger.warning(f"Taalcheck mislukt (origineel gebruikt): {e}")
        return text


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Message]] = []


@app.post("/chat")
async def chat(request: ChatRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "Er is een technisch probleem. Probeer het later opnieuw."}

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0)

        # RAG: haal relevante artikelen op voor deze vraag
        relevant_knowledge = retrieve_articles(request.message, request.history or [])
        system_prompt = SYSTEM_PROMPT_BASE.replace("{ARTICLES}", relevant_knowledge or "(geen specifieke artikelen gevonden)")

        messages = [{"role": m.role, "content": m.content} for m in (request.history or [])]
        messages.append({"role": "user", "content": request.message})

        # Maximaal 20 berichten geschiedenis
        if len(messages) > 20:
            messages = messages[-20:]

        # Zorg dat berichten altijd afwisselen (user/assistant)
        # Verwijder dubbele opeenvolgende rollen om API-fouten te voorkomen
        cleaned = []
        for msg in messages:
            if cleaned and cleaned[-1]["role"] == msg["role"]:
                cleaned[-1] = msg
            else:
                cleaned.append(msg)
        # Anthropic API vereist dat het eerste bericht altijd "user" is
        while cleaned and cleaned[0]["role"] != "user":
            cleaned.pop(0)
        messages = cleaned

        # Retry bij overloaded (529) of rate limit (429) — max 3 pogingen
        import time as _time
        last_error = None
        retry_delays = [2, 6, 15]  # wachttijden in seconden per poging
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    system=[{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    }],
                    messages=messages,
                )
                response_text = response.content[0].text

                # Taalcheck: corrigeer Engelse calques en slechte vertalingen
                response_text = _taalcheck(client, response_text)

                # Strip eventuele [[ESCALATIE]] tag als Nina die toch meestuurt
                tag_match = re.search(r'\[\[ESCALATIE[^\]]*\]\]', response_text)
                if tag_match:
                    response_text = response_text.replace(tag_match.group(0), "").strip()

                # Detecteer escalatie: bevat Nina's antwoord een bevestiging
                # met een e-mailadres dat de gebruiker net heeft gegeven?
                try:
                    _detect_and_escalate(request.message, response_text, messages)
                except Exception as esc_err:
                    logger.error(f"Escalatie-detectie mislukt: {esc_err}")

                return {"response": response_text}
            except anthropic.RateLimitError as e:
                last_error = e
                if attempt < 2:
                    wait = retry_delays[attempt]
                    logger.warning(f"RateLimitError (poging {attempt+1}), retry na {wait}s...")
                    _time.sleep(wait)
                    continue
                raise
            except anthropic.APIStatusError as e:
                last_error = e
                if e.status_code == 529 and attempt < 2:
                    wait = retry_delays[attempt]
                    logger.warning(f"Anthropic overloaded (529, poging {attempt+1}), retry na {wait}s...")
                    _time.sleep(wait)
                    continue
                raise
            except anthropic.APITimeoutError as e:
                last_error = e
                if attempt < 2:
                    logger.warning(f"Timeout (poging {attempt+1}), retry...")
                    _time.sleep(retry_delays[attempt])
                    continue
                raise
        raise last_error

    except anthropic.AuthenticationError:
        return {"error": "Er is een technisch probleem. Probeer het later opnieuw."}
    except anthropic.RateLimitError as e:
        logger.warning(f"RateLimitError na alle retries: {e}")
        return {"error": "Nina is even overbelast. Probeer het over een minuutje opnieuw."}
    except anthropic.APITimeoutError as e:
        logger.warning(f"TimeoutError: {e}")
        return {"error": "Nina reageert even niet. Probeer het over een minuutje opnieuw."}
    except anthropic.APIStatusError as e:
        logger.error(f"APIStatusError {e.status_code}: {e}")
        return {"error": "Er ging iets mis aan onze kant. Probeer het over een minuutje opnieuw."}
    except Exception as e:
        import traceback
        logger.error(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        return {"error": "Er ging iets mis. Probeer het over een minuutje opnieuw."}


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


# ── Help Scout escalatie ───────────────────────────────────────────────────
import json as _json
import requests as _requests

_hs_token_cache = {"access_token": None, "expires_at": 0}

def _hs_get_token():
    """Haal Help Scout OAuth2 access token (cached)."""
    import time as _t
    now = _t.time()
    if _hs_token_cache["access_token"] and now < _hs_token_cache["expires_at"]:
        return _hs_token_cache["access_token"]

    app_id = os.getenv("HELPSCOUT_APP_ID")
    app_secret = os.getenv("HELPSCOUT_APP_SECRET")
    if not app_id or not app_secret:
        return None

    resp = _requests.post("https://api.helpscout.net/v2/oauth2/token", json={
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_secret,
    })
    resp.raise_for_status()
    data = resp.json()
    _hs_token_cache["access_token"] = data["access_token"]
    _hs_token_cache["expires_at"] = now + data["expires_in"] - 60
    return data["access_token"]


def _hs_headers():
    return {"Authorization": f"Bearer {_hs_get_token()}", "Content-Type": "application/json"}


def _format_chat_html(history: list, summary: str) -> str:
    """Format het gesprek als leesbare HTML voor Help Scout."""
    html = f"<p><strong>Samenvatting:</strong> {summary}</p><hr>"
    html += "<h3>Volledig gesprek met Nina</h3>"
    for msg in history:
        role = "Bezoeker" if msg.get("role") == "user" else "Nina"
        color = "#66B0B2" if role == "Nina" else "#555"
        text = msg.get("content", "").replace("\n", "<br>")
        html += f'<p><strong style="color:{color}">{role}:</strong><br>{text}</p>'
    return html


def _detect_and_escalate(user_message: str, nina_response: str, chat_messages: list):
    """Detecteer of Nina een escalatie afrondt en stuur door naar Help Scout.

    Trigger: Nina zegt "doorgestuurd" in haar antwoord → dat betekent dat ze
    klaar is met de escalatieflow (naam+email bevestigd, en bij flow 2 ook
    het onderwerp gevraagd). Op dat moment zoeken we het emailadres op in
    de chathistorie en sturen alles door.
    """
    # Check of Nina's antwoord "doorgestuurd" bevat (= escalatie afronden)
    if not re.search(r'doorgestuurd', nina_response.lower()):
        return

    # Zoek e-mailadres in de chathistorie (uit Nina's bevestigingsvraag of uit user berichten)
    email = None
    name = None

    # Eerst: zoek in Nina's eerdere berichten naar de "klopt dat?" bevestigingsvraag
    for msg in chat_messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content)
        if email_match and re.search(r'(klopt|checken|kloppen|check)', content.lower()):
            email = email_match.group(0).lower()
            # Probeer naam uit datzelfde bericht te halen
            name_match = re.search(r'(?:naam is|naam:)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s-]{1,40}?)(?:\s+en\b|\s*[,.])', content)
            if name_match:
                name = name_match.group(1).strip()

    # Fallback: zoek emailadres in user berichten
    if not email:
        for msg in chat_messages:
            if msg.get("role") != "user":
                continue
            email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', msg.get("content", ""))
            if email_match:
                email = email_match.group(0).lower()
                break

    if not email:
        return

    # Fallback naam: zoek in user berichten
    if not name:
        for msg in chat_messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            for pattern in [
                r'(?:mijn naam is|ik ben|ik heet)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s-]{1,40}?)(?:\s+en\b|\s*[,.]|\s+mijn|\s*$)',
                r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s-]{1,40}?)\s+(?:en\s+)?(?:mijn\s+)?(?:e-?mail|mailadres)',
            ]:
                m = re.search(pattern, content, re.IGNORECASE)
                if m:
                    name = m.group(1).strip()
                    break
            if name:
                break

    if not name:
        name = email.split("@")[0].replace(".", " ").title()

    # Bouw een samenvatting uit de eerste gebruikersvraag
    summary = "Vraag via Nina-chat"
    for msg in chat_messages:
        if msg.get("role") == "user":
            first_q = msg.get("content", "")[:120]
            if first_q and "@" not in first_q:
                summary = first_q
            break

    logger.info(f"Escalatie gedetecteerd: {name} ({email}) — {summary[:60]}")
    _send_escalation(name, email, summary, chat_messages)


def _send_escalation(name: str, email: str, summary: str, chat_messages: list):
    """Stuur escalatie naar Help Scout (aangeroepen vanuit /chat)."""
    token = _hs_get_token()
    if not token:
        logger.error("Help Scout credentials niet geconfigureerd — escalatie niet verstuurd")
        return

    mailbox_id = int(os.getenv("HELPSCOUT_MAILBOX_ID", "0"))
    chat_html = _format_chat_html(chat_messages, summary)

    resp = _requests.post(
        "https://api.helpscout.net/v2/conversations",
        headers=_hs_headers(),
        json={
            "subject": f"Nina-escalatie: {summary[:80]}",
            "customer": {
                "email": email,
                "firstName": name,
            },
            "mailboxId": mailbox_id,
            "type": "email",
            "status": "active",
            "imported": False,
            "tags": ["nina-escalatie"],
            "threads": [
                {
                    "type": "customer",
                    "customer": {"email": email},
                    "text": chat_html,
                }
            ],
        },
    )
    resp.raise_for_status()
    logger.info(f"Escalatie aangemaakt in Help Scout: {name} ({email})")


@app.get("/health")
async def health():
    return {"status": "ok"}
