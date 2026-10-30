from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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


@app.get("/")
async def root():
    return FileResponse("static/helpcenter.html")


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
    """Laad alle artikelen (publiek + intern) als geïndexeerde documenten voor RAG.
    Publiek = knowledge/articles/, intern = knowledge/articles-intern/.
    Beide zijn beschikbaar voor Nina's prompt; alleen publieke worden via /api/articles naar het Helpcentrum gestuurd."""
    index = []
    for dir_name, is_public in [("knowledge/articles", True), ("knowledge/articles-intern", False)]:
        articles_dir = Path(dir_name)
        if not articles_dir.exists():
            continue
        for file in sorted(articles_dir.glob("*.md")):
            content = file.read_text(encoding="utf-8")
            title = _extract_title(content, file.stem)
            collection = _extract_collection(content)
            tags = _extract_tags(content)
            clean_lines = [
                l for l in content.splitlines()
                if not l.startswith("COLLECTIE:") and not l.startswith("TAGS:")
            ]
            clean_content = "\n".join(clean_lines).strip()
            # Subkopjes (## ...) apart indexeren: dit zijn vaak losse deelvragen
            headings = [
                re.sub(r"^#+\s*", "", l).strip()
                for l in content.splitlines()
                if l.lstrip().startswith("##")
            ]
            headings = [h for h in headings if h and "gerelateerde" not in h.lower()]
            index.append({
                "id": file.stem,
                "title": title,
                "collection": collection,
                "tags": tags,
                "headings": headings,
                "content": content,
                "clean_content": clean_content,
                "public": is_public,
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

# Laad ook kleine kennisbestanden in root (bijv. aanmeldlinks-en-anchors.md)
def load_base_knowledge() -> str:
    knowledge_dir = Path("knowledge")
    parts = []
    # Laad root-bestanden (anchorlinks, jaarkalender, etc.)
    for file in sorted(knowledge_dir.glob("*.md")):
        parts.append(file.read_text(encoding="utf-8"))
    # Laad alleen universele gedragsregels — content-specifieke instructies
    # worden via RAG geladen zodat ze alleen meekomen als ze relevant zijn.
    # ⚑ Harde regels gaan vóór alle andere instructies en worden als eerste geladen.
    # De rest van ALTIJD_LADEN komt daarna in alfabetische volgorde binnen, dus zonder
    # deze lijst zou een harde regel ergens in het midden van het blok belanden.
    HARDE_REGELS = [
        "lesboeken-en-readers-komen-per-mail.md",
    ]
    ALTIJD_LADEN = {
        "gesprek-afsluiten.md",
        "schrijfstijl.md",
        "doorvragen-voor-informatie.md",
        "past-bij-mij-vragen.md",
        "kortingen-kosten.md",
        "whatsapp-telefoonnummer.md",
        "naam-email-verzamelen.md",
        "scope-hallucinatie-escalatie.md",
        "gespreksvoering.md",
        "startdata-klassikaal.md",
        "koopsignaal-gegevens-verzamelen.md",
        "restorative-yoga-vs-alignment.md",
        "kennisblogs-alleen-op-verzoek.md",
        "workshop-gaat-door-en-aanmelden.md",
        "verboden-woorden.md",
        "geen-geverifieerde-link.md",
        "docenten-per-workshop.md",
        "alternatieve-combinaties.md",
        "modulair-korting-intern.md",
        "gezondheid-blessure-escalatie.md",
        "consumentenprogrammas-escalatie.md",
        "ryt500-instroom-met-elders-behaalde-200.md",
        "afmelden-en-annuleren-uitvragen.md",
        "studie-adviesgesprek.md",
        "open-dagen-kennismaken.md",
        "contextsignalen-herkennen.md",
        "vyn-en-yoga-alliance-verschil.md",
        "gesprek-mailen.md",
    }
    instructies_dir = knowledge_dir / "instructies"
    if instructies_dir.exists():
        for naam in HARDE_REGELS:
            harde_regel = instructies_dir / naam
            if harde_regel.exists():
                parts.insert(0, harde_regel.read_text(encoding="utf-8"))
            else:
                logger.warning(f"HARDE REGEL ONTBREEKT: {naam}")
        for file in sorted(instructies_dir.glob("*.md")):
            if file.name in ALTIJD_LADEN or file.name in HARDE_REGELS:
                if file.name in HARDE_REGELS:
                    continue  # al vooraan ingevoegd
                parts.append(file.read_text(encoding="utf-8"))
    return "\n\n".join(parts)

BASE_KNOWLEDGE = load_base_knowledge()


# ── RAG: haal relevante artikelen op ───────────────────────────────────────
def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower())


def _bouw_woordgewichten(index: list) -> dict:
    """Hoe zeldzamer een woord in de kennisbank, hoe zwaarder het weegt.

    ⚑ 31-8-2026. Zonder dit telde elk woord even zwaar. Gevolg: een breed
    overzichtsartikel dat élke productnaam opsomt, scoorde op alledaagse woorden als
    "module", "yoga" en "lesmodules" — die in bijna elk artikel staan — en verdrong
    daarmee het artikel dat werkelijk over die ene module gaat. Bij een vraag als
    "wat leer ik in de module Yoga Nidra" won de catalogus van "Lesdoelen: Yoga Nidra".
    Het onderscheidende woord in die vraag is "nidra", niet "module".
    """
    import math
    totaal = len(index) or 1
    voorkomen: dict = {}
    for artikel in index:
        tekst = normalize(" ".join([
            artikel["title"], " ".join(artikel["tags"]),
            " ".join(artikel.get("headings", [])), artikel["clean_content"],
        ]))
        for woord in set(tekst.split()):
            voorkomen[woord] = voorkomen.get(woord, 0) + 1
    # Ondergrens 0.15: een alledaags woord mag verzwakken, maar nooit tot nul —
    # anders verdwijnt bij een vraag die alleen uit gewone woorden bestaat élk signaal.
    return {w: max(0.15, math.log(totaal / (1 + n))) for w, n in voorkomen.items()}


WOORDGEWICHTEN = _bouw_woordgewichten(ARTICLES_INDEX)
# Onbekend woord (bv. een typefout of een synoniem dat nergens staat): behandel als
# zeldzaam, niet als alledaags. Anders zou een uniek woord juist worden weggedrukt.
GEWICHT_ONBEKEND = 2.0


def _weegt(woord: str) -> float:
    return WOORDGEWICHTEN.get(woord, GEWICHT_ONBEKEND)


def _komt_voor(woord: str, tekst: str) -> bool:
    """Losse cijfers alleen als heel woord; de rest als deelreeks.

    ⚑ 31-8-2026. Cijfers werden hiervoor helemaal weggegooid door de len>2-filter,
    waardoor "Yin Yoga 1" en "Yin Yoga 2" niet te onderscheiden waren. Ze meenemen mag
    echter niet als deelreeks: "1" zit ook in "2021" en in "10". Vandaar de woordgrens.
    Voor gewone woorden blijft de deelreeks staan — die vangt meervouden gratis op
    ("opleiding" vindt "opleidingen").
    """
    if woord.isdigit():
        return re.search(rf"(?<!\d){re.escape(woord)}(?!\d)", tekst) is not None
    return woord in tekst


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


def retrieve_articles(query: str, history: List, top_k: int = 3):
    """Selecteer de meest relevante artikelen op basis van de vraag + recent gesprek.

    Retourneert (kennis_tekst, [artikeltitels]) — de titels worden in het weeklog
    vastgelegd zodat Sandy kan zien waarop Nina haar antwoord baseerde.
    """
    # Combineer huidige vraag + laatste 3 berichten voor context
    search_text = query
    for msg in history[-6:]:
        search_text += " " + msg.content
    # len>2 laat losse cijfers vallen; die zijn hier juist onderscheidend
    # ("Yin Yoga 1" vs "Yin Yoga 2"). _komt_voor matcht ze op woordgrens.
    base_words = [w for w in normalize(search_text).split()
                  if len(w) > 2 or w.isdigit()]
    words = expand_with_synonyms(base_words)

    if not words:
        return "", []

    base_words_set = set(base_words)

    scored = []
    for article in ARTICLES_INDEX:
        score = 0
        title_norm = normalize(article["title"])
        tags_norm = normalize(" ".join(article["tags"]))
        headings_norm = normalize(" ".join(article.get("headings", [])))
        content_norm = normalize(article["clean_content"])

        for word in words:
            # Synoniemen scoren de helft van directe matches
            multiplier = 1.0 if word in base_words_set else 0.5
            # ...en alles weegt mee naar zeldzaamheid: "nidra" zegt meer dan "module".
            multiplier *= _weegt(word)
            if _komt_voor(word, title_norm):
                score += 8 * multiplier
            if _komt_voor(word, headings_norm):
                # subkopjes zijn vaak losse deelvragen: zwaar meewegen
                score += 5 * multiplier
            if _komt_voor(word, tags_norm):
                score += 4 * multiplier
            if _komt_voor(word, content_norm):
                score += 1 * multiplier

        # Titeldekking: hoeveel van de vraag staat er in de TITEL van dit artikel?
        # ⚑ 31-8-2026. Zonder dit verliest een specifiek artikel van een zusartikel dat
        # dezelfde algemene woorden deelt. "Wat is het LIVE+ Support Pack?" verloor van
        # "Wat zijn de verschillen tussen de Support Packs?", omdat "support" en "pack"
        # in beide staan en het onderscheidende "live" te licht woog. Een titel die de
        # héle vraag dekt, hoort te winnen van een titel die er de helft van dekt.
        # Bereik 10-25 geeft dezelfde uitslag (breed gemeten over alle artikelen), dus
        # 12 staat niet op een mesrand.
        if base_words:
            gewicht_totaal = sum(_weegt(w) for w in base_words) or 1
            titeldekking = sum(_weegt(w) for w in base_words
                               if _komt_voor(w, title_norm)) / gewicht_totaal
            score += 12 * titeldekking

        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    relevant = [a for _, a in scored[:top_k]]

    if not relevant:
        return "", []

    parts = []
    titles = []
    for a in relevant:
        parts.append(f"## {a['title']}\n(Collectie: {a['collection']})\n\n{a['clean_content']}")
        titles.append(a["title"])
    return "\n\n---\n\n".join(parts), titles


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
- Sluit een antwoord af met één slotalinea. Na inhoudelijke informatie over een opleiding, module, studieroute, data of prijzen is dat het mailaanbod (begint met "Zal ik dit gesprek even naar je mailen?"). Kies je slot volgens de instructie "het gesprek naar de bezoeker mailen": daar staat wanneer het aanbod je slot is en welke verplichte slotvraag voorgaat. Anders, waar passend, een open deur: "Heb je nog vragen, dan help ik je graag verder."
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
8. Bied NOOIT aan om te bellen of terug te bellen. Het enige contactkanaal is e-mail: "Sandy neemt via e-mail contact met je op." Nooit "Sandy belt je", "belt of mailt", of "wil je gebeld worden".
9. Gok NOOIT productnamen, oorzaken, datums of gegevens die de bezoeker niet zelf gaf. Gaat het over een account, toegang, bestelling of betaling die jij niet kunt inzien? Ga dat niet zelf diagnosticeren en verzin geen oorzaak ("je twee jaar is vast om"). Neem kort over wat de bezoeker zegt en escaleer meteen naar Sandy, zij kan het in de administratie zien.

## Vragen stellen

- Stel nooit meer dan één vervolgvraag per bericht
- Kies de meest bepalende vraag als je meer informatie nodig hebt
- Gooi nooit meerdere vragen tegelijk op de bezoeker
- Het mailaanbod telt als je ene vraag. Bied je het aan, stel dan in dat bericht geen andere vervolgvraag. Moet je eerst iets verduidelijken (bijvoorbeeld klassikaal of online), stel dan die ene verduidelijkingsvraag, eventueel na de korte uitleg die de doorvraaginstructie voorschrijft. Het mailaanbod volgt pas onder je inhoudelijke antwoord.

## Doorvragen — verplicht bij algemene interesse of brede vragen

Bij ELKE vraag over een opleiding, yoga-stijl of aanbod waarbij nog niet duidelijk is of de bezoeker klassikaal of online wil: ALTIJD eerst doorvragen. Nooit meteen een volledig aanbod doen.

Verplichte doorvraag bij:
- "Wat is yin/yang/ashtanga yoga?" of vergelijkbare uitlegvragen → geef een korte uitleg, vraag dan: "Leuk dat je geïnteresseerd bent! Gaat je voorkeur uit naar klassikaal of online?"
- "Is er een gratis proefles?" of vergelijkbare oriënterende vragen → NIET antwoorden met opties, maar EERST vragen: "Gaat je interesse uit naar de klassikale lessen of een online opleiding?" Gaat het om klassikaal of om kennismaken, volg dan de instructie "open-dagen-kennismaken"
- "Wat kost het?" zonder verdere context → "Gaat je vraag over een online of klassikale opleiding?"
- Elke vraag waarbij klassikaal vs. online nog onbekend is

Geef nooit een overzicht van opties (online kijklessen, klassikale dag, etc.) voordat je weet of de bezoeker klassikaal of online wil.

## Wanneer de Studieadvieswijzer aanbevelen

Verwijs naar de **Studieadvieswijzer** (https://sanayou.com/studieadvieswijzer) als laagdrempelige eerste stap wanneer:
- De bezoeker vraagt "welke opleiding past bij mij?" of vergelijkbare oriënterende vragen
- Iemand zich oriënteert op het aanbod en nog geen specifieke richting heeft gekozen
- Iemand wil weten welke modules of trajecten aansluiten bij wat ze zoeken

De Studieadvieswijzer is een interactieve tool die in een paar vragen persoonlijk studieadvies geeft. Geen afspraak nodig, de bezoeker kan het direct zelf doorlopen.

Volgorde van adviseren:
1. **Eerst:** beantwoord concrete vragen direct waar mogelijk
2. **Bij oriëntatievraag of "wat past bij mij":** verwijs naar https://sanayou.com/studieadvieswijzer
3. **Bij een warme lead, een open-dagen-/kennismakingsvraag, of als de bezoeker zelf om een gesprek vraagt:** bied het Calendly-studieadviesgesprek aan, mét de vraag om naam en mailadres (zie hieronder en de instructies "studie-adviesgesprek" en "open-dagen-kennismaken")

## Wanneer wel en niet de Calendly-link geven

Geef de link naar het gratis studieadviesgesprek (https://calendly.com/sanayou-sandy/studie-advies-gesprek) in deze situaties (verruimd door Sandy, 20-7-2026):
- Warme lead: de bezoeker toont serieuze interesse in een opleiding (koopsignalen: keuze gemaakt of aan het maken, startwens, twijfel tussen twee routes, vragen over inschrijven of betalen). Dan mag je het gesprek proactief aanbieden, nadat je eerst inhoudelijk hebt geholpen
- Een vraag naar open dagen, een proefles of kennismaken (volg de instructie "open-dagen-kennismaken")
- De bezoeker heeft de Studieadvieswijzer al gedaan en wil aanvullend persoonlijk advies
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

"""

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
Pas NIETS anders aan — geen stijl, geen inhoud, geen opmaak.
Engelse product- en modulenamen zijn eigennamen en vertaal je NOOIT: Your Body Your Yoga, Yin Yoga Basics, Yang Yoga Basics, Restorative Alignment, Critical Alignment, The Art of Hands-on Assists, Teaching Assessment Pack, LIVE+, Teacher Training."""

# Vangnet (19-10-2026): de taalcheck vertaalde "Your Body Your Yoga" twee keer naar
# "Jouw Lichaam Jouw Yoga". Productnaam deterministisch terugzetten.
_PRODUCTNAAM_VANGNET = re.compile(r"\b[Jj]ouw [Ll]ichaam,? [Jj]ouw [Yy]oga\b")


def _productnamen_vangnet(text: str) -> str:
    return _PRODUCTNAAM_VANGNET.sub("Your Body Your Yoga", text) if text else text


def _taalcheck(client: anthropic.Anthropic, text: str) -> str:
    """Corrigeer Engelse calques in Nina's antwoord. Tijdslimiet 5s, bij fout origineel teruggeven."""
    # Lege/whitespace tekst NOOIT naar het model sturen: het antwoordt dan met zijn
    # eigen intro ("stuur me de tekst!"), die anders naar de bezoeker lekt (13-7).
    if not text or not text.strip():
        return text
    try:
        check_client = anthropic.Anthropic(api_key=client.api_key, timeout=2.0)
        result = check_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": f"{TAALCHECK_PROMPT}\n\nTekst:\n{text}"}],
        )
        corrected = result.content[0].text.strip()
        if corrected and corrected != text:
            logger.info("Taalcheck: correctie toegepast")
        return _productnamen_vangnet(corrected if corrected else text)
    except Exception as e:
        logger.warning(f"Taalcheck mislukt (origineel gebruikt): {e}")
        return _productnamen_vangnet(text)


# ── Vangnet: Nina biedt NOOIT aan te bellen of terug te bellen ──────────────
# Het enige escalatiekanaal is e-mail (zie whatsapp-telefoonnummer.md +
# naam-email-verzamelen.md). Deze regels staan in de altijd-geladen instructies,
# maar zijn ooit toch overtreden ("Sandy zal je snel bellen of mailen").
# Dit is de harde, deterministische borging: alleen affirmatieve bel-aanbiedingen
# worden naar e-mail herschreven; negatieve zinnen ("telefonisch niet bereikbaar")
# blijven ongemoeid. Geen API-call, dus geen extra latency.
_CALL_OFFER_PATTERNS = [
    (re.compile(r"\bbellen of (e-?)?mailen\b", re.I), "mailen"),
    (re.compile(r"\bbelt of (e-?)?mailt\b", re.I), "mailt"),
    (re.compile(r"\bgebeld of gemaild\b", re.I), "gemaild"),
    (re.compile(r"\bmailen of bellen\b", re.I), "mailen"),
    (re.compile(r"\bmailt of belt\b", re.I), "mailt"),
    (re.compile(r"\bbelt je(\s+(snel|zo|straks|binnenkort|even))?\s+terug\b", re.I), "mailt je"),
    (re.compile(r"\bbelt je(\s+(snel|zo|straks|binnenkort|even))?\b", re.I), "mailt je"),
    (re.compile(r"\b(zal|gaat)\s+je\s+(snel\s+|zo\s+|even\s+)?bellen\b", re.I), r"\1 je mailen"),
    (re.compile(r"\bneemt telefonisch contact\b", re.I), "neemt per e-mail contact"),
    (re.compile(r"\bterug te bellen\b", re.I), "te mailen"),
    (re.compile(r"\bje terugbelt\b", re.I), "je mailt"),
    (re.compile(r"\bterugbellen\b", re.I), "mailen"),
]


def _contactkanaal_vangnet(text: str) -> str:
    """Herschrijf bel-/terugbel-aanbiedingen naar e-mail. Faalt nooit, blokkeert nooit."""
    fired = False
    for pat, repl in _CALL_OFFER_PATTERNS:
        new = pat.sub(repl, text)
        if new != text:
            fired = True
            text = new
    if fired:
        logger.info("Contactkanaal-vangnet: bel-aanbod herschreven naar e-mail")
    return text


# ── Vangnet: het mailaanbod staat één keer per gesprek ─────────────────────
# Staat de aanbodzin al in een eerder bericht van Nina (alleen role == "assistant" telt), dan gaat een herhaald
# aanbod eruit. Precies begrensd: de aanbodzin plus de zinnen van Sandy's aanbodtekst die er direct en in volgorde
# op volgen (opmaak en regeleinden ertussen tellen mee). Alle andere tekst, ervóór en erna, blijft staan. Blijft er
# niets over, dan wordt het antwoord de open deur uit de vaste prompt. Een herformuleerd aanbod herkent dit niet.
# Geen API-call. De tekst moet gelijk blijven aan gesprek-mailen.md; tests/test_chatmail_r2.py toetst dat.
MAILAANBOD = "Zal ik dit gesprek even naar je mailen?"
MAILAANBOD_VERVOLG = (
    "Dan heb je alles bij elkaar.",
    "Heb ik iets over het hoofd gezien, dan sturen we je nog een aanvulling.",
    "En als jij daarna nog een vraag hebt, reageer je gewoon op die mail.",
)
OPEN_DEUR = "Heb je nog vragen, dan help ik je graag verder."


def _mailaanbod_patroon():
    tussen = r"[ \t\r\n*_]*"
    staart = ""
    for zin in reversed(MAILAANBOD_VERVOLG):
        staart = "(?:" + tussen + re.escape(zin) + staart + ")?"
    rand = r"[*_\"“”]*"
    return re.compile(r"[ \t]*" + rand + re.escape(MAILAANBOD) + staart + rand + r"[ \t]*")


_MAILAANBOD_RE = _mailaanbod_patroon()


def _naad(m) -> str:
    """Vervanging voor één weggehaald aanbod: een spatie als er op dezelfde regel aan beide kanten tekst staat."""
    s = m.string
    links = s[m.start() - 1] if m.start() > 0 else "\n"
    rechts = s[m.end()] if m.end() < len(s) else "\n"
    return " " if links not in "\r\n" and rechts not in "\r\n" else ""


def _mailaanbod_eenmalig(text: str, history) -> str:
    """Haal een herhaald mailaanbod weg. Faalt nooit, blokkeert nooit."""
    try:
        if MAILAANBOD not in text:
            return text
        if not any(getattr(m, "role", "") == "assistant" and MAILAANBOD in (getattr(m, "content", "") or "")
                   for m in (history or [])):
            return text
        houden = []
        for alinea in re.split(r"\n\s*\n", text.strip()):
            if MAILAANBOD in alinea:
                alinea = _MAILAANBOD_RE.sub(_naad, alinea)
                alinea = re.sub(r"[ \t]*\n(?:[ \t]*\n)+", "\n", alinea).strip()
                if not re.search(r"\w", alinea):
                    continue
            houden.append(alinea)
        logger.info("Mailaanbod-vangnet: herhaald aanbod weggehaald")
        return "\n\n".join(houden) if houden else OPEN_DEUR
    except Exception as e:
        logger.warning(f"Mailaanbod-vangnet faalde: {type(e).__name__}")
        return text


# ── Vangnet: Nina verzint NOOIT een checkout-/aanmeldlink ──────────────────
# Een gegokte checkout.sanayou.com-slug stuurt een koopklare bezoeker naar een
# dode of verkeerde pagina (dodelijk). De instructie verbiedt dit al, maar is
# ooit toch overtreden (verzonnen link naar yoga-nidra-opleiding-online). Dit is
# de harde borging: elke checkout.sanayou.com-URL die NIET letterlijk in
# aanmeldlinks-en-anchors.md staat, wordt uit het antwoord verwijderd en
# vervangen door een veilige zin. Geen API-call, dus geen extra latency.
def _load_checkout_allowlist() -> set:
    allow = set()
    try:
        raw = (Path("knowledge") / "aanmeldlinks-en-anchors.md").read_text(encoding="utf-8")
        for m in re.finditer(r"https?://checkout\.sanayou\.com/\S+", raw):
            allow.add(m.group(0).rstrip("`).,;"))
    except Exception as e:
        logger.warning(f"Checkout-allowlist niet geladen: {e}")
    return allow


CHECKOUT_ALLOWLIST = _load_checkout_allowlist()
logger.info(f"Checkout-allowlist geladen: {len(CHECKOUT_ALLOWLIST)} geverifieerde links")
_CHECKOUT_URL_RE = re.compile(r"https?://checkout\.sanayou\.com/\S+")
_CHECKOUT_FALLBACK = (
    "die exacte aanmeldlink laat ik je even door Sandy toesturen. Wat is je naam en "
    "e-mailadres? Dan stuurt zij je vandaag de juiste link"
)


def _checkout_link_vangnet(text: str) -> str:
    """Verwijder verzonnen checkout-links die niet in de bron van waarheid staan. Faalt nooit."""
    fired = False

    def _repl(match):
        nonlocal fired
        url = match.group(0).rstrip("`).,;")
        if url in CHECKOUT_ALLOWLIST:
            return match.group(0)
        fired = True
        return _CHECKOUT_FALLBACK

    new = _CHECKOUT_URL_RE.sub(_repl, text)
    if fired:
        logger.warning("Checkout-vangnet: verzonnen checkout-link verwijderd")
    return new


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Message]] = []
    session_id: Optional[str] = None


@app.post("/chat")
async def chat(request: ChatRequest, http_request: Request = None):
    # http_request levert alleen het IP voor de bron-grens van de claimdienst (als
    # sha256, nooit het IP zelf). FastAPI vult hem in; een directe aanroep zonder
    # (zoals de tests) geeft None en dan is de bron leeg, wat het contract toestaat.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "Er is een technisch probleem. Probeer het later opnieuw."}

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0)

        # RAG: haal relevante artikelen op voor deze vraag
        relevant_knowledge, used_articles = retrieve_articles(request.message, request.history or [])
        articles_section = relevant_knowledge or "(geen specifieke artikelen gevonden)"

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
                    model="claude-sonnet-5",
                    max_tokens=2048,
                    # Sonnet 5 zet adaptief 'thinking' AAN als je deze parameter weglaat.
                    # Dat vrat het hele token-budget op → alleen een thinking-blok, geen
                    # tekstantwoord (leeg → taalcheck-lek, gesprek 13-7 16:09). Nina is een
                    # support-chatbot (kennis wordt vóór de call opgehaald, geen tool-loop),
                    # dus thinking uit = sneller, goedkoper, en het hele budget gaat naar
                    # het antwoord. Zie claude-api-skill: budget_tokens bestaat niet meer op
                    # Sonnet 5; {type:"disabled"} mag wél.
                    thinking={"type": "disabled"},
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT_BASE,
                            "cache_control": {"type": "ephemeral"},
                        },
                        {
                            "type": "text",
                            "text": f"## Kennisbank (relevante artikelen voor deze vraag)\n\n{articles_section}",
                        },
                    ],
                    messages=messages,
                )
                # Pak het eerste TEKST-blok. Sonnet kan een ThinkingBlock vooraan
                # zetten; blind content[0].text pakken crasht dan (geen .text).
                response_text = next(
                    (b.text for b in response.content if getattr(b, "type", None) == "text"),
                    "",
                )

                # Leeg antwoord (bv. alleen een ThinkingBlock, geen tekstblok):
                # NIET doorgeven aan de nabewerkers. De taalcheck kreeg dan een lege
                # tekst en antwoordde met zijn eigen intro ("stuur me de tekst!"),
                # die naar de bezoeker lekte (gesprek 13-7 16:09). Nette fallback.
                if not response_text.strip():
                    logger.warning("Leeg modelantwoord (mogelijk alleen ThinkingBlock) — fallback gebruikt")
                    response_text = ("Sorry, daar ging even iets mis aan mijn kant. "
                                     "Kun je je vraag nog een keer stellen?")

                # Beide tags worden gelezen en weggehaald vóór de taalcheck. Dan hangt
                # niets af van een model dat een tag zou kunnen herschrijven of wissen.
                # ⚑ r2 (Jordan F2, 21-10-2026): de escalatietrigger is één boolean, hier
                # één keer vastgelegd op de ruwe modeltekst. Dezelfde waarde bepaalt
                # hieronder de route én `force`. In r1 opende de ruwe tag de route, maar
                # kwam `force` uit de tekst ná de taalcheck; wiste die de tag, dan ging de
                # escalatie stil verloren.
                escalatie_trigger = bool(_ESCALATIE_TAG_RE.search(response_text))
                response_text = _strip_escalatie_tag(response_text)
                response_text, had_chatmail_tag = _strip_chatmail_tag(response_text)

                # Taalcheck: corrigeer Engelse calques en slechte vertalingen
                response_text = _taalcheck(client, response_text)
                # Restjes weghalen (bv. een model dat zelf een tag verzint); de trigger
                # hierboven verandert daardoor niet meer.
                response_text, _ = _strip_chatmail_tag(response_text)
                response_text = _strip_escalatie_tag(response_text)

                # Vangnet: nooit bellen/terugbellen aanbieden, alleen e-mail
                response_text = _contactkanaal_vangnet(response_text)

                # Vangnet: nooit een verzonnen checkout-link laten staan
                response_text = _checkout_link_vangnet(response_text)

                # Vangnet: het mailaanbod staat één keer per gesprek
                response_text = _mailaanbod_eenmalig(response_text, request.history)

                # De modeltekst zoals hij was vóór de chatmail-afronding. De escalatie-
                # detectie krijgt deze tekst, net als in r1, zodat het vangnet op
                # "doorgestuurd" e.d. niet verandert doordat hieronder een zin wegvalt.
                model_tekst = response_text

                # Gesprek mailen. ⚑ r2 (Jordan C1/F1): een tag of een modelzin is een
                # VERZOEK om te mailen, geen bewijs. De server bepaalt de zichtbare zin uit
                # de geverifieerde uitkomst. Het gesprek gaat ongekort mee (request.history),
                # niet de tot 20 berichten ingekorte `messages`.
                chatmail = None
                volledig = [{"role": m.role, "content": m.content} for m in (request.history or [])]
                volledig.append({"role": "user", "content": request.message})
                if had_chatmail_tag:
                    chatmail = _verwerk_chatmail(volledig, response_text,
                                                 session_id=request.session_id,
                                                 bron=_bron_hash(http_request),
                                                 modeltekst_houden=escalatie_trigger)
                    response_text = chatmail["antwoord"]
                else:
                    # Geen tag = geen verzending in deze beurt. Zegt het model toch dat
                    # het gesprek gemaild is, dan gaat die zin eruit (J1). In de
                    # mailbevestigingsfase (nu een bevestigd adres) met de ruimere herkenning.
                    fase = bool(_adresstatus(volledig)["bevestigd"])
                    response_text = _zonder_onbewezen_claim(response_text, fase=fase)

                # Detecteer escalatie: tag (primair) óf Nina rondt af met een afsluitende
                # formulering + een e-mailadres in het gesprek (vangnet, alleen zonder tags).
                # Een chatmailbeurt zonder escalatietrigger is nooit een escalatie: haar
                # bevestigde adres staat in het gesprek, dus het vangnet zou op een zin als
                # "ik heb het doorgestuurd" een tweede ticket maken.
                escalated = False
                if escalatie_trigger or not had_chatmail_tag:
                    try:
                        escalated = bool(_detect_and_escalate(
                            request.message, model_tekst, messages, force=escalatie_trigger))
                    except Exception as esc_err:
                        logger.error(f"Escalatie-detectie mislukt: {esc_err}")

                # Leg de gespreksbeurt vast in het weeklog (faalt stil, blokkeert Nina nooit)
                _log_conversation(
                    request.session_id, request.message, response_text, used_articles, escalated,
                    chat_messages=messages,
                    chatmail=bool(chatmail and chatmail["verstuurd"]),
                )

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
    """Publieke Helpcentrum API. Leest ALLEEN uit knowledge/articles/.
    Bestanden eindigend op '-intern.md' worden defensief uitgesloten."""
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
        if file.stem.endswith("-intern"):
            continue
        content = file.read_text(encoding="utf-8")
        first_heading = next((l for l in content.splitlines() if l.startswith("#")), "").lower()
        if any(marker in first_heading for marker in ["intern", "interne", "instructie nina", "instructie voor nina"]):
            continue
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

# Bovengrenzen (r2) voor elke wacht op een externe dienst, als requests-timeout
# (verbinden, lezen) in seconden. Dat is per verbindingspoging en per leespauze,
# geen wandklok-totaal; zie de LEESMIJ voor het gemeten slechtste geval.
HS_TIMEOUT = (5, 15)
# De websiteroute begrenst het MailBlue-deel zelf op 9 s; Nina wacht daar ruim boven.
REGISTRATIE_TIMEOUT = (5, 12)
# De claimdienst wacht zelf tot 10 s op zijn lock (waitLock(10000)); de leesgrens ligt
# daar ruim boven, anders geeft Nina op terwijl de claim nog loopt.
CLAIM_TIMEOUT = (5, 20)

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

    # timeout (r2): elke wacht op een externe dienst heeft een bovengrens.
    resp = _requests.post("https://api.helpscout.net/v2/oauth2/token", json={
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_secret,
    }, timeout=HS_TIMEOUT)
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


def _detect_and_escalate(user_message: str, nina_response: str, chat_messages: list, force: bool = False):
    """Detecteer of Nina een escalatie afrondt en stuur door naar Help Scout.

    Primaire trigger: ``force=True`` (Nina zette de onzichtbare [[ESCALATIE]]
    tag in haar antwoord). Dat signaal is taal-onafhankelijk en dus robuust.

    Vangnet: ook als de tag ontbreekt maar Nina afrondt met een herkenbare
    afsluitende formulering ("doorgestuurd", "doorgegeven", "genoteerd",
    "neemt contact"), proberen we alsnog te escaleren. Het e-mailadres-vereiste
    verderop voorkomt valse triggers bij gewone "genoteerd"-zinnen zonder gegevens.
    """
    afsluit_patroon = r'doorgestuurd|doorgegeven|doorgespeeld|genoteerd|neemt (?:zo )?(?:snel )?(?:mogelijk )?contact|laat (?:sandy|haar) contact'
    if not force and not re.search(afsluit_patroon, nina_response.lower()):
        return
    # ⚑ 30-10-2026 (Rosita, gesprek 27-10 10:29): een beurt die nog om gegevens of bevestiging vraagt, is nooit
    # een afronding. De bevestigingsvraag zegt sindsdien "Pas als je dit bevestigt, stuur ik het door naar Sandy".
    # Omschrijft het model dat tot "wordt doorgestuurd" of "neemt contact", dan maakte het vangnet al een ticket met
    # een onbevestigd adres. De tag (force) blijft de primaire trigger. Alleen echte vraagvormen: "je naam en
    # e-mailadres" staat ook in een afronding ("ik heb je naam en e-mailadres doorgegeven"), review Jordan 30-10 F1.
    vraagt_nu_om_gegevens = r'mag ik je naam|wat is je e-?mail|klopt dat\?'
    if not force and re.search(vraagt_nu_om_gegevens, nina_response.lower()):
        logger.info("Escalatie-vangnet overgeslagen: Nina vraagt nog om gegevens of bevestiging")
        return

    # Dubbele-escalatie-guard: is er in een EERDERE beurt van deze conversatie al
    # een escalatie AFGEROND? Dan geen tweede Help Scout-ticket voor dezelfde klant.
    # (De [[ESCALATIE]]-tag is uit de historie gestript, dus we herkennen een eerdere
    # afronding aan Nina's zichtbare afsluitzin. 1 escalatie per gesprek is genoeg.)
    # LET OP: alleen VOLTOOIDE-tijd markers ("doorgestuurd", "doorgegeven"). NIET
    # "neemt contact" — die belofte spreekt Nina óók uit terwijl ze nog om naam+mail
    # vraagt, en dan onderdrukte de guard de échte escalatie erna (gebeurd bij Josje,
    # gesprek 7-7: geen ticket omdat een eerdere ask-beurt al "neemt contact" bevatte).
    # ⚑ 14-9-2026: 'door aan sandy' stond hier ook in, en dat is GEEN afronding maar een
    # belofte ("zodra ik dat heb, geef ik het door aan Sandy"). Die zin spreekt Nina juist uit
    # terwijl ze nog om naam en e-mailadres vraagt, waarna de guard de echte afronding erna
    # onderdrukte. Gemeten bij Daphne (gesprek 13-9 19:05): drie beurten, keurig naam en
    # e-mailadres bevestigd, "ik heb je verzoek doorgestuurd naar Sandy" als slot, en tóch geen
    # Help Scout-ticket. Zelfde vorm als de Josje-bug hierboven met "neemt contact".
    # Alleen voltooide deelwoorden blijven over, en een beurt waarin Nina nog om gegevens
    # vraagt telt nooit als afronding, ook niet als daar "genoteerd" in staat
    # ("ik heb je naam genoteerd, wat is je e-mailadres?").
    reeds_patroon = r'doorgestuurd|doorgegeven|doorgespeeld|genoteerd'
    vraagt_nog_gegevens = r'mag ik je naam|wat is je e-?mail|je naam en e-?mail|klopt dat\?'
    for msg in chat_messages:
        if msg.get("role") != "assistant":
            continue
        inhoud = msg.get("content", "").lower()
        if re.search(reeds_patroon, inhoud) and not re.search(vraagt_nog_gegevens, inhoud):
            logger.info("Escalatie overgeslagen: al eerder afgerond in deze conversatie")
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

    # Bouw een samenvatting uit de laatste INHOUDELIJKE bezoekersvraag.
    # Was: het eerste bezoekersbericht uit chat_messages. Dat leek veilig, maar de
    # opschoonlus in /chat vouwt twee opeenvolgende user-berichten samen tot het
    # LAATSTE ("cleaned[-1] = msg"), en de trim houdt alleen de laatste 20 beurten.
    # Beide kunnen de openingsvraag uit chat_messages laten verdwijnen, waarna het
    # eerste bezoekersbericht "Ja" is — vandaar de Help Scout-tickets met als
    # onderwerp "Nina-escalatie: Ja" (Olivia, gemeten 1-9-2026).
    summary = "Vraag via Nina-chat"
    kandidaat = _inhoudelijke_vraag(chat_messages, user_message)
    if kandidaat and "@" not in kandidaat:
        summary = kandidaat[:120]
    else:
        for msg in chat_messages:
            if msg.get("role") == "user":
                first_q = msg.get("content", "")[:120]
                if first_q and "@" not in first_q:
                    summary = first_q
                break

    logger.info(f"Escalatie gedetecteerd: {name} ({email}) — {summary[:60]}")
    _send_escalation(name, email, summary, chat_messages)
    return True


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
        timeout=HS_TIMEOUT,  # r2: ook in een beurt met beide tags wacht niets onbegrensd
    )
    resp.raise_for_status()
    logger.info(f"Escalatie aangemaakt in Help Scout: {name} ({email})")


# ── Gesprek mailen (Sandy, 21-10-2026; r2 na Jordans review van 21-10) ─────────
# Nina biedt aan het gesprek te mailen. Zegt de bezoeker ja, dan vraagt en bevestigt
# Nina naam en e-mailadres (zelfde vorm als de escalatie) en zet zij [[CHATMAIL]] op
# haar afsluitende bericht. Die tag is een VERZOEK, geen bewijs. Dan, in deze volgorde:
#   1. alleen naar het adres dat NU bevestigd is (_adresstatus): elk nieuw of afwijkend
#      adres ontkracht een eerdere bevestiging, ook na een ja (Jordan F3);
#   2. een atomaire claim bij de claimdienst (Apps Script, CLAIMDIENST-CONTRACT.md) op
#      sleutel HMAC-SHA256(CLAIM_SECRET, 'chatmail|' + sessie + '|' + adres): dezelfde actie herhaald of
#      tegelijk geeft hooguit één verzendpoging (Jordan F4). Geen claimdienst of een
#      claimfout = geen mail (fail closed);
#   3. het gesprek gaat als NIEUW UITGAAND Help Scout-gesprek vanaf de academy-mailbox
#      naar de bezoeker, op gesloten; een antwoord van de bezoeker heropent het bij ons;
#   4. pas na een geslaagde mail: registratie in MailBlue via de website (route B, Tonny
#      22-10: Nina houdt geen MailBlue-sleutel; brontag en Hoofdlijst liggen daar vast),
#      daarna `bevestig` bij de claimdienst, met registratie ok of mislukt;
#   5. de beurt krijgt in de Nina-log de markering "gesprek gemaild".
# De zichtbare zin komt van de server, uit de uitkomst (Jordan C1/F1): alleen een in
# deze beurt geslaagde verzending laat "ik heb ons gesprek naar je gemaild" staan, en
# `al_verstuurd` van de claimdienst geeft de zin dat het al eerder gemaild is.
# Een woordmatch op de historie bewijst niets meer (r1 deed dat; J3 sloeg zo de eerste
# mail over).

import hashlib as _hashlib
import hmac as _hmac
import time as _time_mod
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone

_CHATMAIL_TAG_RE = re.compile(r'`*\s*\[\[CHATMAIL[^\]]*\]\]\s*`*')
_ESCALATIE_TAG_RE = re.compile(r'`*\s*\[\[ESCALATIE[^\]]*\]\]\s*`*')
CHATMAIL_HS_TAG = "nina-chatmail"

# Begeleidende tekst: Carrie, 21-10-2026, letterlijk overgenomen uit
# ~/.flashbag/output/carrie/sandy-schrijverskamer/2026-10-21/privacy-en-groet/
# chatmail-nina-zonder-groet.txt (sha256 4a49fbea6d91ed6b1fb2f337b192748a127cf4874cfbed5ca46eca10b30cca7e).
# Zonder groet: de mail leest onder Sandy's Help Scout-handtekening (besluit Olivia 21-10,
# punt 2). Eén alinea per regel; "\n\n".join(...) + "\n" is byte-gelijk aan dat bestand.
CHATMAIL_ONDERWERP = "Ons gesprek van vandaag, om rustig terug te lezen"
CHATMAIL_BEGELEIDING = (
    "Hallo {voornaam},",
    "Zoals beloofd: hieronder staat je gesprek met Nina, onze digitale assistent, zodat je "
    "alles nog eens rustig kunt teruglezen.",
    "We lezen het ook zelf nog even na. Heeft Nina iets over het hoofd gezien, of zie ik voor "
    "jou nog een andere mogelijkheid, dan sturen we je een aanvulling.",
    "En heb je daarna nog een vraag, reageer dan gewoon op deze mail. Dan komt hij "
    "rechtstreeks bij ons terecht.",
)

# ── Wat de bezoeker ziet. De server kiest de zin uit de uitkomst. ───────────────
# Afronding bij een in DEZE beurt geslaagde mail: de zin uit de instructie
# gesprek-mailen.md (vastgesteld door Olivia, 21-10-2026).
CHATMAIL_AFRONDING = "Top, ik heb ons gesprek naar je gemaild."
# Bevestigingsvraag: dezelfde vorm als stap 2 van de vastgestelde instructie. De server
# stelt hem zelf als de bezoeker een ander adres noemt (F3).
CHATMAIL_BEVESTIGINGSVRAAG = "Even checken: je naam is {naam} en je e-mailadres is {email}, klopt dat?"
# ── Meldingen: Carrie, definitief 21-10-2026 (team-log 2026-10-21-retour-jared-negen-korte-meldingen-
# chatmail-en-wijzer-carrie.md; bestanden in ~/.flashbag/output/carrie/sandy-schrijverskamer/2026-10-21/
# nina-wijzer-negen-meldingen/, zonder slotregel-einde). N1, N2, N3, N5 en N6 staan er BYTE-GELIJK in;
# de test toetst elke constante tegen de sha256 van het bestand (en tegen het bestand zelf als het er is).
# N1 (sha256 8534a35dad6ee7a95c2d82974d9860598c69f1294af769f429d6090aa8d90d76). Help Scout weigerde zeker (HTTP 400-499), of de claimdienst/
# Help Scout is niet ingesteld of gaf een fout.
CHATMAIL_MISLUKT = 'Het is me nu niet gelukt om ons gesprek te mailen. Wil je het straks nog een keer vragen? Dan probeer ik het opnieuw.'
# N2 (sha256 a0124a90c608ed9ea510615d1a567f7963e056adfa8ad09330f34e6a3a183370). Geen adres dat nu bevestigd is, en geen nieuw adres om te bevestigen.
CHATMAIL_GEEN_ADRES = 'Ik heb je e-mailadres nog niet goed binnen. Wil je het nog een keer typen? Dan kijken we samen even of het klopt.'
# N3 (sha256 76626f21dbc09fa9975360de729e0590cccb66013e5a6d4e082f2fc312551260). Claimdienst: al_verstuurd.
# De eerste zin is zelf een verzendclaim (bewezen door `al_verstuurd`). De server zet hem ná het
# filteren neer, en een volgende beurt leest de geschiedenis niet als bewijs (test meetpunt 1).
CHATMAIL_AL_VERSTUURD = 'Ik heb ons gesprek al eerder naar je gemaild. Zie je de mail niet? Kijk dan even bij je spam of in het tabblad Reclame.'
# N4 (sha256 b0ca73cc2ce1cf1eaba1194756568755b1a35c32b362bf7daded845b01cc1bf6), letterlijk. "We kijken het ook na."
# staat er sinds 23-10 weer in: de dagelijkse onzeker-controle (08:30, eigenaar Olivia) leest ook de Nina-rijen en
# draait aantoonbaar; dat was Carrie's voorwaarde. Claimdienst: bezig, of Help Scout gaf geen antwoord (time-out,
# netwerkfout) of een 5xx: het kan zijn dat de mail weg is, of niet.
CHATMAIL_ONDERWEG = 'Ik weet nog niet zeker of de mail met ons gesprek goed is aangekomen. Kijk over een paar minuten even in je inbox. We kijken het ook na. Je hoeft het dus niet opnieuw te vragen.'
# N5 (sha256 6c4e7d6d699b0cd4cb496efe066024fefd3760416c1378794d482abd363e0d12). Claimdienst: limiet_ontvanger/limiet_bron.
CHATMAIL_LIMIET = 'Ik kan ons gesprek nu even niet mailen. Probeer het later nog eens.'
# N6 (sha256 1e21c064a16005404c4887c2b3b97d31f85920c697939f0096b904a956eeb402). Het model zei zonder tag dat het gesprek gemaild is:
# in deze beurt ging niets weg (J1); of het eerder wél gebeurde weet de server zonder claim niet.
CHATMAIL_NIET_VERSTUURD = 'Ik kan hier niet zien of ons gesprek al naar je gemaild is. Wil je het gemaild krijgen? Zeg het dan even.'

CHATMAIL_LOGMARKERING = " · 📧 gesprek gemaild"

# Zelfde vormen als in _detect_and_escalate. Bewust geen gedeelde helper: de escalatie
# valt terug op een onbevestigd adres uit de bezoekersberichten, een chatmail nooit.
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
_BEVESTIGINGSVRAAG_RE = re.compile(r'klopt|checken|kloppen|check', re.I)
_NAAM_IN_BEVESTIGING_RE = re.compile(
    r'(?:naam is|naam:)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s-]{1,40}?)(?:\s+en\b|\s*[,.])')
_JA_WOORDEN = {"ja", "jaa", "jazeker", "jawel", "yes", "klopt", "correct", "juist",
               "precies", "goed", "prima", "top", "ok", "oke", "oké", "okay", "akkoord", "zeker"}
_NEE_WOORDEN = {"nee", "neen", "niet", "fout", "verkeerd", "anders", "ander", "nope"}
# "Nee, mail het toch maar niet" na een ja: een ontkenning over het adres of het mailen.
_ADRESVERWIJZING_RE = re.compile(r'\b(?:e-?mail\w*|mail\w*|adres\w*|gemaild)\b', re.I)

# Een zin die zegt dat het gesprek al gemaild/verstuurd IS (voltooid). Gebruikt om zo'n
# zin weg te halen als er geen bewezen verzending achter zit. Nooit om een aflevering
# te bewijzen. ⚑ Dit is een BEGRENSDE herkenning met regels, geen garantie over vrije
# modeltekst. Twee standen (r2.3, Jordan closing C1/F1):
#   - gewoon: bij verstuurd/verzonden/gestuurd/toegestuurd moet er een ontvanger bij staan
#     ("naar je", "per mail" …), zodat gewone antwoorden niet onterecht een zin verliezen;
#   - mailbevestigingsfase (er is NU een bevestigd adres): elk voltooid verzendwerkwoord
#     telt, ook zonder ontvanger en met "je" vóór het onderwerp ("Het gesprek is verzonden.",
#     "Ik heb je de chat verstuurd."), en ook "de mail" als onderwerp; behalve als er een
#     andere ontvanger staat ("naar Sandy"): dat is een escalatiezin.
_CLAIM_ONDERWERP_RE = re.compile(r'\b(?:gesprek|gesprekje|chatgesprek|chat)\b', re.I)
_CLAIM_ONDERWERP_FASE_RE = re.compile(r'\b(?:gesprek|gesprekje|chatgesprek|chat|mail|e-mail|mailtje)\b', re.I)
_CLAIM_ANDERE_ONTVANGER_RE = re.compile(
    r'\b(?:naar|aan)\s+(?:sandy|het team|ons team|een collega|haar)\b', re.I)
_CLAIM_VOLTOOID_RE = re.compile(
    r'\b(?:heb|hebben|is|zijn|werd|werden|ging)\b(?P<tussen>[^.!?\n]{0,80}?)'
    r'\b(?P<ww>gemaild|doorgemaild|onderweg|verstuurd|toegestuurd|verzonden|gestuurd)\b', re.I)
_CLAIM_STAAT_IN_RE = re.compile(r'\bstaat\b(?P<tussen>[^.!?\n]{0,60}?)\bin je (?:mail|inbox|mailbox)\b', re.I)
_CLAIM_ONTVANGER_RE = re.compile(
    r'\b(?:naar je|naar jou|aan je|aan jou|je toe|in je (?:mail|inbox|mailbox)|per (?:e-?)?mail)\b', re.I)
_ONTKENNING_RE = re.compile(r'\b(?:niet|geen|nooit)\b', re.I)

# Registratie in MailBlue loopt via de website (Tonny, MailBlue-eigenaar, 22-10-2026, route B): Nina's
# Vercel-project heeft geen MailBlue-sleutel en krijgt er geen. De route zet zelf brontag 454 [BRON] Nina-chat
# en Hoofdlijst 80 (alleen als er nog geen lijstrecord is). Nina stuurt alleen naam en adres, ondertekend met
# het claimgeheim dat beide projecten delen; het doelvoorvoegsel scheidt dit van de claimsleutels.
# Contract: sanayou-pages api/nina-registratie.js (tekenTekst); gedeelde testvector in beide testreeksen.
REGISTRATIE_URL = "https://sanayou.com/api/nina-registratie"
REGISTRATIE_DOEL = "nina-registratie|v1"

# Claimdienst (CLAIMDIENST-CONTRACT.md). URL en secret komen uit de Vercel-omgeving:
# CLAIM_URL en CLAIM_SECRET. Ontbreekt één van beide, dan gaat er geen mail uit.
CLAIM_SOORT = "chatmail"
CLAIM_UITKOMSTEN = {"geclaimd", "al_verstuurd", "bezig", "limiet_ontvanger", "limiet_bron"}


class ClaimFout(Exception):
    """De claimdienst gaf geen geldig antwoord: geen mail (fail closed)."""


class OnzekereVerzending(Exception):
    """Het verzoek aan Help Scout vertrok, maar er kwam geen antwoord (time-out of
    netwerkfout). De mail kan weg zijn: niet als mislukt melden, niet opnieuw proberen."""


def _strip_chatmail_tag(text: str):
    """(tekst zonder [[CHATMAIL]], stond de tag erin)."""
    if not text:
        return text, False
    gevonden = bool(_CHATMAIL_TAG_RE.search(text))
    if gevonden:
        text = _CHATMAIL_TAG_RE.sub(" ", text).strip()
    return text, gevonden


def _strip_escalatie_tag(text: str) -> str:
    """Tekst zonder [[ESCALATIE]]. Of de tag er stond, legt de aanroeper vóór de taalcheck vast."""
    if not text:
        return text
    return _ESCALATIE_TAG_RE.sub(" ", text).strip() if _ESCALATIE_TAG_RE.search(text) else text


def _is_claimzin(zin: str, fase: bool = False) -> bool:
    """Zegt deze zin dat het gesprek al gemaild/verstuurd IS? Een toekomst ("je krijgt ons
    gesprek per mail"), een aanbod of een ontkenning ("nog niet gemaild") telt niet.
    `fase=True`: de mailbevestigingsfase, zie de uitleg bij _CLAIM_ONDERWERP_FASE_RE."""
    onderwerp = _CLAIM_ONDERWERP_FASE_RE if fase else _CLAIM_ONDERWERP_RE
    if not zin or not onderwerp.search(zin):
        return False
    for m in _CLAIM_VOLTOOID_RE.finditer(zin):
        if _ONTKENNING_RE.search(m.group("tussen")):
            continue
        if m.group("ww").lower() in ("gemaild", "doorgemaild", "onderweg") or _CLAIM_ONTVANGER_RE.search(zin):
            return True
        if fase and not _CLAIM_ANDERE_ONTVANGER_RE.search(zin):
            return True
    for m in _CLAIM_STAAT_IN_RE.finditer(zin):
        if not _ONTKENNING_RE.search(m.group("tussen")):
            return True
    return False


def _zonder_claimzinnen(text: str, fase: bool = False):
    """(tekst zonder zinnen die een verzending melden, aantal weggehaald). Alinea's blijven."""
    if not text:
        return text, 0
    regels, weg = [], 0
    for regel in text.split("\n"):
        zinnen = re.split(r'(?<=[.!?])\s+', regel)
        houden = [z for z in zinnen if not _is_claimzin(z, fase)]
        weg += len(zinnen) - len(houden)
        if houden or not regel.strip():
            regels.append(" ".join(houden))
    tekst = re.sub(r'\n{3,}', '\n\n', "\n".join(regels)).strip()
    return tekst, weg


def _met_uitkomstzin(text: str, zin: str) -> str:
    """Servertekst voor een [[CHATMAIL]]-beurt: wat er van de modeltekst over is (zonder
    verzendclaims, herkend in de mailbevestigingsfase) met de zin van de server erachter.
    Zonder modeltekst (de gewone chatmailbeurt) is het alleen de zin van de server."""
    rest, _ = _zonder_claimzinnen(text or "", fase=True)
    return f"{rest}\n\n{zin}" if rest else zin


def _zonder_onbewezen_claim(text: str, fase: bool = False) -> str:
    """Beurt zonder [[CHATMAIL]]: er ging niets weg. Een zin die toch zegt dat het gesprek
    gemaild is, gaat eruit en de bezoeker hoort eerlijk wat de server weet.
    `fase=True` (er is nu een bevestigd adres): de ruimere herkenning."""
    rest, weg = _zonder_claimzinnen(text or "", fase=fase)
    if not weg:
        return text
    logger.warning(f"Chatmail-claim zonder tag weggehaald ({weg} zin, fase={fase}): er ging geen mail uit")
    return f"{rest}\n\n{CHATMAIL_NIET_VERSTUURD}" if rest else CHATMAIL_NIET_VERSTUURD


def _adresstatus(berichten: list) -> dict:
    """De actuele adresbevestiging, als begrensde toestand (r2, Jordan F3).

    Loopt het gesprek in volgorde door:
    - Nina's bevestigingsvraag met precies één adres opent een nieuwe vraag en maakt elke
      eerdere bevestiging ongeldig;
    - het eerste bezoekersbericht daarna beslist: een ja zonder ontkenning en zonder ander
      adres bevestigt; noemt het een ander adres ("Ja, stuur het naar nieuw@…"), dan is er
      niets bevestigd en is dat nieuwe adres het adres om opnieuw te bevestigen;
    - ná een ja maakt elk nieuw of afwijkend adres, of een ontkenning over het adres of het
      mailen, de bevestiging weer ongeldig.
    Alleen een nieuwe ja op een nieuwe bevestigingsvraag ontsluit een ander adres.

    Geeft {"bevestigd": (naam, email) of None, "nieuw": email of None, "naam": str of None}.
    """
    vraag = None        # (naam, email) uit de openstaande bevestigingsvraag
    bevestigd = None
    naam = None         # laatst bekende naam uit een bevestigingsvraag
    nieuw = None        # door de bezoeker genoemd adres dat nog bevestigd moet worden
    for m in berichten:
        rol = m.get("role")
        inhoud = m.get("content") or ""
        adressen = {x.lower() for x in _EMAIL_RE.findall(inhoud)}
        if rol == "assistant":
            if adressen and _BEVESTIGINGSVRAAG_RE.search(inhoud):
                bevestigd, nieuw = None, None
                if len(adressen) != 1:
                    vraag = None  # twee adressen in één vraag: niets is ondubbelzinnig
                    continue
                n = _NAAM_IN_BEVESTIGING_RE.search(inhoud)
                if n:
                    naam = n.group(1).strip()
                vraag = (n.group(1).strip() if n else None, next(iter(adressen)))
            elif adressen and (bevestigd is None or adressen != {bevestigd[1]}):
                bevestigd, vraag = None, None
            continue
        if rol != "user":
            continue
        woorden = set(re.sub(r"[^\w\s]", " ", inhoud.lower()).split())
        if vraag is not None:
            if adressen and adressen != {vraag[1]}:
                anders = adressen - {vraag[1]}
                bevestigd, nieuw = None, (next(iter(anders)) if len(anders) == 1 else None)
            elif (woorden & _JA_WOORDEN) and not (woorden & _NEE_WOORDEN):
                bevestigd, nieuw = vraag, None
            else:
                bevestigd = None
            vraag = None
            continue
        if adressen:
            if bevestigd is None or adressen != {bevestigd[1]}:
                anders = adressen - ({bevestigd[1]} if bevestigd else set())
                bevestigd, nieuw = None, (next(iter(anders)) if len(anders) == 1 else None)
        elif bevestigd is not None and (woorden & _NEE_WOORDEN) and _ADRESVERWIJZING_RE.search(inhoud):
            bevestigd = None
    if bevestigd:
        email = bevestigd[1]
        bevestigd = (bevestigd[0] or email.split("@")[0].replace(".", " ").title(), email)
    return {"bevestigd": bevestigd, "nieuw": nieuw, "naam": naam}


def _bevestigde_gegevens(berichten: list):
    """(naam, e-mail) als het adres NU bevestigd is, anders (None, None).

    Een chatmail gaat alleen naar een adres dat de bezoeker zelf heeft bevestigd. Nooit
    terugvallen op een los adres uit de bezoekersberichten.
    """
    status = _adresstatus(berichten)
    return status["bevestigd"] if status["bevestigd"] else (None, None)


def _voornaam(naam: str) -> str:
    """Eerste woord van wat de bezoeker als naam gaf ("Petra de Vries" -> "Petra")."""
    delen = (naam or "").split()
    return delen[0] if delen else ""


def _claimhash(tekst: str) -> str:
    """HMAC-SHA256 (hex, 64 tekens) met CLAIM_SECRET als sleutel, voor `sleutel`, `ontvanger` en
    `bron` (contract § Sleutels, aangescherpt 21-10 20:4x). Een kale sha256 van een mailadres of
    IPv4 is met een woordenlijst terug te rekenen door wie alleen de Sheet ziet; dit niet zonder
    het secret. Zonder secret geen hash: ClaimFout (fail closed)."""
    secret = os.getenv("CLAIM_SECRET")
    if not secret:
        raise ClaimFout("CLAIM_SECRET ontbreekt")
    return _hmac.new(secret.encode("utf-8"), tekst.encode("utf-8"), _hashlib.sha256).hexdigest()


def _datum_amsterdam(nu=None) -> str:
    """JJJJ-MM-DD in Europe/Amsterdam, zonder tz-database (die is op Vercel niet zeker).
    EU-zomertijd: van de laatste zondag van maart 01:00 UTC tot de laatste zondag van
    oktober 01:00 UTC is het UTC+2, anders UTC+1. De test vergelijkt dit met zoneinfo."""
    nu = (nu or _datetime.now(_timezone.utc)).astimezone(_timezone.utc)

    def laatste_zondag(maand):
        d = _datetime(nu.year, maand, 31, 1, 0, tzinfo=_timezone.utc)
        return d - _timedelta(days=(d.weekday() + 1) % 7)

    zomer = laatste_zondag(3) <= nu < laatste_zondag(10)
    return (nu + _timedelta(hours=2 if zomer else 1)).strftime("%Y-%m-%d")


def _chatmail_sleutel(session_id, email: str, nu=None) -> str:
    """Claimsleutel: één mail per Nina-sessie per adres (contract).
    `session_id` is Optional in ChatRequest. Zonder sessie: één mail per adres per dag
    (Europe/Amsterdam), zodat ook dan herhalen of tegelijk versturen één mail geeft."""
    adres = (email or "").strip().lower()
    if session_id and str(session_id).strip():
        return _claimhash(f"chatmail|{session_id}|{adres}")
    return _claimhash(f"chatmail|geen-sessie|{adres}|{_datum_amsterdam(nu)}")


def _bron_hash(http_request) -> str:
    """HMAC van het bezoekers-IP (eerste x-forwarded-for, anders x-real-ip), of '' (ook zonder
    CLAIM_SECRET; dan mailt Nina toch niet). Het IP zelf gaat nooit naar buiten."""
    try:
        headers = getattr(http_request, "headers", None)
        if headers is None:
            return ""
        ip = (headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
             (headers.get("x-real-ip") or "").strip()
        return _claimhash(ip) if ip else ""
    except Exception:
        return ""


def _claimdienst(bericht: dict) -> dict:
    """Eén POST naar de claimdienst. Geldig alleen bij HTTP 2xx én JSON én ok === true
    (contract); al het andere is een ClaimFout. Het secret komt nooit in een log."""
    url, secret = os.getenv("CLAIM_URL"), os.getenv("CLAIM_SECRET")
    if not url or not secret:
        raise ClaimFout("claimdienst niet ingesteld (CLAIM_URL/CLAIM_SECRET)")
    try:
        r = _requests.post(url, json={"secret": secret, **bericht}, timeout=CLAIM_TIMEOUT)
    except Exception as e:
        raise ClaimFout(f"geen antwoord: {type(e).__name__}") from e
    status = getattr(r, "status_code", 0)
    if not isinstance(status, int) or not 200 <= status < 300:
        raise ClaimFout(f"HTTP {status}")
    try:
        data = r.json()
    except Exception as e:
        raise ClaimFout("geen geldige JSON") from e
    if not isinstance(data, dict) or data.get("ok") is not True:
        fout = data.get("error") if isinstance(data, dict) else type(data).__name__
        raise ClaimFout(f"ok is niet true ({str(fout)[:40]})")
    return data


def _claim_chatmail(sleutel: str, ontvanger: str, bron: str) -> str:
    data = _claimdienst({"actie": "claim", "soort": CLAIM_SOORT, "sleutel": sleutel,
                         "ontvanger": ontvanger, "bron": bron or ""})
    uitkomst = data.get("uitkomst")
    if uitkomst not in CLAIM_UITKOMSTEN:
        raise ClaimFout(f"onbekende uitkomst {str(uitkomst)[:30]!r}")
    return uitkomst


def _bevestig_claim(sleutel: str, status: str, kenmerk="", registratie="") -> bool:
    """`bevestig` naar de claimdienst. Faalt nooit naar buiten; False = niet vastgelegd
    (de rij blijft dan `bezig`: onzeker, nooit stil opnieuw verstuurd)."""
    try:
        _claimdienst({"actie": "bevestig", "sleutel": sleutel, "status": status,
                      "kenmerk": str(kenmerk or ""), "registratie": registratie})
        return True
    except Exception as e:
        logger.error(f"Chatmail-claim niet bevestigd ({status}): {type(e).__name__}: {e}")
        return False


def _chatmail_html(naam: str, berichten: list) -> str:
    """De mail aan de bezoeker: begeleidende tekst + het gesprek, alles ge-escaped."""
    import html as _html

    def opmaak(tekst: str) -> str:
        veilig = _html.escape(tekst.strip())
        veilig = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', veilig)
        veilig = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', veilig)
        return veilig.replace("\n", "<br>")

    delen = [f"<p>{opmaak(alinea.format(voornaam=_voornaam(naam)))}</p>" for alinea in CHATMAIL_BEGELEIDING]
    delen.append("<hr>")
    for m in berichten:
        tekst = (m.get("content") or "").strip()
        if not tekst:
            continue
        wie = "Jij" if m.get("role") == "user" else "Nina"
        kleur = "#555555" if wie == "Jij" else "#66B0B2"
        delen.append(f'<p><strong style="color:{kleur}">{wie}:</strong><br>{opmaak(tekst)}</p>')
    return "\n".join(delen)


def _stuur_chatmail(naam: str, email: str, berichten: list) -> int:
    """Nieuw uitgaand Help Scout-gesprek, op gesloten. Geeft het gesprek-id (0 = onbekend).

    Zeker mislukt (er ging niets weg, of Help Scout antwoordde 400-499): gewone exception.
    Onzeker (het verzoek vertrok; geen antwoord, een 5xx, of 1xx/3xx): OnzekereVerzending.
    ⚑ Contract aangescherpt 21-10 19:5x: een 5xx (bv. 502/504 van een gateway) zegt niet of het
    gesprek al is aangemaakt; als `mislukt` vastleggen zou een herhaling een tweede mail kunnen geven.
    """
    if not _hs_get_token():  # een fout hier is zeker: het gesprek is nog niet verstuurd
        raise RuntimeError("Help Scout-sleutels ontbreken")
    mailbox_id = int(os.getenv("HELPSCOUT_MAILBOX_ID", "0") or 0)
    if not mailbox_id:
        raise RuntimeError("HELPSCOUT_MAILBOX_ID ontbreekt")
    headers = _hs_headers()
    body = {
        "subject": CHATMAIL_ONDERWERP,
        "customer": {"email": email, "firstName": naam},
        "mailboxId": mailbox_id,
        "type": "email",
        "status": "closed",
        "imported": False,
        "tags": [CHATMAIL_HS_TAG],
        "threads": [{"type": "reply", "customer": {"email": email},
                     "text": _chatmail_html(naam, berichten)}],
    }
    try:
        resp = _requests.post("https://api.helpscout.net/v2/conversations",
                              headers=headers, json=body, timeout=HS_TIMEOUT)
    except Exception as e:
        raise OnzekereVerzending(type(e).__name__) from e
    status = getattr(resp, "status_code", 0)
    if isinstance(status, int) and 400 <= status <= 499:
        raise RuntimeError(f"Help Scout HTTP {status}")
    if not isinstance(status, int) or not 200 <= status < 300:
        raise OnzekereVerzending(f"HTTP {status}")
    try:
        return int(resp.headers.get("Resource-Id") or 0)
    except Exception:
        return 0  # de mail is weg; een onleesbaar kenmerk maakt dat niet ongedaan


def _registratie_handtekening(geheim: str, ts: str, email: str, naam: str) -> str:
    tekst = f"{REGISTRATIE_DOEL}|{ts}|{email.strip().lower()}|{naam}"
    return _hmac.new(geheim.encode("utf-8"), tekst.encode("utf-8"), _hashlib.sha256).hexdigest()


def _chatmail_naar_mailblue(naam: str, email: str) -> str:
    """Registreert de bezoeker via de websiteroute. Geeft de uitkomst van de website; elke
    andere afloop is een exception (de aanroeper legt dan `registratie: mislukt` vast)."""
    geheim = os.getenv("CLAIM_SECRET")
    if not geheim:
        raise RuntimeError("CLAIM_SECRET ontbreekt")
    naam = " ".join((naam or "").split())
    ts = str(int(_time_mod.time()))
    r = _requests.post(REGISTRATIE_URL, timeout=REGISTRATIE_TIMEOUT, allow_redirects=False, json={
        "naam": naam, "email": email.strip(), "ts": ts,
        "mac": _registratie_handtekening(geheim, ts, email, naam)})
    try:
        data = r.json()
    except Exception:
        data = None
    if r.status_code != 200 or not isinstance(data, dict) or data.get("ok") is not True:
        fout = data.get("fout") if isinstance(data, dict) else None
        raise RuntimeError(f"website HTTP {r.status_code}" + (f": {str(fout)[:80]}" if fout else ""))
    return str(data.get("uitkomst") or "geregistreerd")[:200]


def _verwerk_chatmail(berichten: list, antwoord: str, session_id=None, bron: str = "",
                      modeltekst_houden: bool = False) -> dict:
    """Voert één [[CHATMAIL]]-verzoek uit. Faalt nooit naar buiten; geeft de zichtbare
    tekst voor de bezoeker.

    ⚑ r2.3 (Jordan closing C1/F1): wat de bezoeker over de verzending leest is SERVERtekst
    uit de gemeten status. Bij een gewone chatmailbeurt is het antwoord alleen die zin; de
    modeltekst telt niet mee. Alleen als er in dezelfde beurt ook geëscaleerd wordt
    (`modeltekst_houden=True`) blijven de modelzinnen staan die geen verzendclaim zijn
    (bv. "Ik heb je vraag doorgestuurd naar Sandy."), met de serverzin erachter.
    """
    basis = antwoord if modeltekst_houden else ""
    uitkomst = {"antwoord": antwoord, "verstuurd": False, "reden": "", "conversation_id": None,
                "mailblue": "", "claim": "", "bevestigd": None}

    status = _adresstatus(berichten)
    if not status["bevestigd"]:
        if status["nieuw"] and status["naam"]:
            zin = CHATMAIL_BEVESTIGINGSVRAAG.format(naam=status["naam"], email=status["nieuw"])
            reden = "ander adres genoemd: opnieuw bevestigen"
        else:
            zin, reden = CHATMAIL_GEEN_ADRES, "geen bevestigd adres"
        uitkomst.update(antwoord=_met_uitkomstzin(basis, zin), reden=reden)
        logger.warning(f"Chatmail niet verstuurd: {reden}")
        return uitkomst
    naam, email = status["bevestigd"]

    # Zonder Help Scout-instellingen gaat er nooit een mail uit: dan ook niet claimen.
    if not (os.getenv("HELPSCOUT_APP_ID") and os.getenv("HELPSCOUT_APP_SECRET")
            and os.getenv("HELPSCOUT_MAILBOX_ID")):
        uitkomst.update(antwoord=_met_uitkomstzin(basis, CHATMAIL_MISLUKT),
                        reden="Help Scout niet ingesteld")
        logger.error("Chatmail niet verstuurd: Help Scout niet ingesteld")
        return uitkomst

    try:
        sleutel = _chatmail_sleutel(session_id, email)
        claim = _claim_chatmail(sleutel, _claimhash(email.strip().lower()), bron)
    except Exception as e:  # ClaimFout of iets onverwachts: fail closed
        uitkomst.update(antwoord=_met_uitkomstzin(basis, CHATMAIL_MISLUKT),
                        reden=f"claimfout: {e}", claim="fout")
        logger.error(f"Chatmail niet verstuurd, claimfout: {type(e).__name__}: {e}")
        return uitkomst
    uitkomst["claim"] = claim
    if claim != "geclaimd":
        zin = {"al_verstuurd": CHATMAIL_AL_VERSTUURD, "bezig": CHATMAIL_ONDERWEG}.get(claim, CHATMAIL_LIMIET)
        uitkomst.update(antwoord=_met_uitkomstzin(basis, zin), reden=f"claimdienst: {claim}")
        logger.info(f"Chatmail niet opnieuw verstuurd: claimdienst zegt {claim}")
        return uitkomst

    succes = _met_uitkomstzin(basis, CHATMAIL_AFRONDING)
    try:
        uitkomst["conversation_id"] = _stuur_chatmail(naam, email, berichten + [
            {"role": "assistant", "content": succes}])
    except OnzekereVerzending as e:
        # Niet bevestigen: de rij blijft `bezig`. Het overzicht toont hem na 10 min als
        # onzeker; een herhaling krijgt `bezig` en verstuurt niets.
        uitkomst.update(antwoord=_met_uitkomstzin(basis, CHATMAIL_ONDERWEG),
                        reden=f"onzeker: {e}")
        logger.error(f"Chatmail onzeker (geen antwoord van Help Scout): {e}")
        return uitkomst
    except Exception as e:
        # Zeker mislukt: vastleggen als `mislukt`, dan geeft een volgende claim weer
        # `geclaimd` (de veilige herhaalroute).
        uitkomst["bevestigd"] = _bevestig_claim(sleutel, "mislukt")
        uitkomst.update(antwoord=_met_uitkomstzin(basis, CHATMAIL_MISLUKT),
                        reden=f"verzending mislukt: {type(e).__name__}")
        logger.error(f"Chatmail niet verstuurd: {type(e).__name__}: {e}")
        return uitkomst

    uitkomst.update(verstuurd=True, antwoord=succes)
    logger.info(f"Chatmail verstuurd via Help Scout (gesprek {uitkomst['conversation_id']})")
    try:
        uitkomst["mailblue"] = _chatmail_naar_mailblue(naam, email)
        registratie = "ok"
        logger.info(f"Chatmail MailBlue: {uitkomst['mailblue']}")
    except Exception as e:
        uitkomst["mailblue"] = f"mislukt: {type(e).__name__}"
        registratie = "mislukt"
        logger.error(f"Chatmail MailBlue mislukt (mail is wel verstuurd): {type(e).__name__}: {e}")
    uitkomst["bevestigd"] = _bevestig_claim(sleutel, "verstuurd", uitkomst["conversation_id"] or "",
                                            registratie)
    return uitkomst


# ── De inhoudelijke vraag van een gesprek ──────────────────────────────────
# Nina vraagt bij onduidelijkheid door; de bezoeker antwoordt dan "Ja". Juist die
# beurt is de beurt die als vastloper of escalatie gelogd wordt. Wie het LAATSTE
# bezoekersbericht wegschrijft, logt dus de bevestiging en niet de vraag — en dan
# meet de maandelijkse kennisgaten-analyse niets bij precies de belangrijkste bron.
# Gemeten door Olivia op 1-9-2026: 5 van de 5 vastlopers in augustus hadden als
# vraag letterlijk "Ja", "ja klopt", "Ja dat klopt"; twee Help Scout-tickets heetten
# "Nina-escalatie: Ja".
#
# Daarom: loop de bezoekersberichten ACHTERSTEVOREN door en neem het eerste dat
# geen kale bevestiging is. Dat is cause-onafhankelijk — het werkt ook als de
# historie onderweg is ingekort of samengevouwen.

# Losse WOORDEN (niet frasen): de toets kijkt per woord, dus "dank je wel" moet
# als "dank", "je" en "wel" herkenbaar zijn, niet als één sleutel.
_BEVESTIGINGEN = {
    "ja", "nee", "ok", "oke", "oké", "okay", "top", "bedankt", "dank", "dankje",
    "dankjewel", "dankuwel", "klopt", "prima", "goed", "duidelijk", "helder",
    "yes", "jazeker", "jawel", "nope", "graag", "mooi", "super", "fijn",
    "perfect", "akkoord", "precies", "inderdaad", "zeker", "juist", "correct",
    "begrepen", "snap", "snapte", "gelukt", "thanks", "dankuwel", "oke",
}


def _is_kale_bevestiging(tekst: str) -> bool:
    """True als dit bericht alleen instemming/afwijzing draagt, geen vraag."""
    kaal = re.sub(r"[^\w\s]", " ", (tekst or "").lower())
    woorden = kaal.split()
    if not woorden:
        return True
    # "ja", "ja klopt", "ja dat klopt", "ja helemaal juist" — kort én opgebouwd
    # uit louter bevestigende woorden of vulwoorden.
    if len(woorden) > 5:
        return False
    vulwoorden = {"dat", "het", "helemaal", "wel", "echt", "heel", "en", "is", "hoor",
                  "even", "maar", "nou", "ja", "zo", "die", "u", "je", "jou", "ik",
                  "denk", "al", "dus", "toch", "hem", "ze", "er"}
    return all(w in _BEVESTIGINGEN or w in vulwoorden for w in woorden)


def _inhoudelijke_vraag(chat_messages, laatste_bericht=""):
    """De laatste ECHTE bezoekersvraag uit dit gesprek.

    Volgorde: (1) achterstevoren het eerste bezoekersbericht dat geen kale
    bevestiging is, (2) anders het eerste bezoekersbericht van de sessie,
    (3) anders het laatste bericht zelf. Nooit leeg als er iets gezegd is.
    """
    bezoeker = [
        (m.get("content") or "") if isinstance(m, dict) else (getattr(m, "content", "") or "")
        for m in (chat_messages or [])
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if laatste_bericht and (not bezoeker or bezoeker[-1] != laatste_bericht):
        bezoeker.append(laatste_bericht)

    for tekst in reversed(bezoeker):
        if tekst.strip() and not _is_kale_bevestiging(tekst):
            return tekst.strip()
    for tekst in bezoeker:
        if tekst.strip():
            return tekst.strip()
    return (laatste_bericht or "").strip()


def _log_conversation(session_id, user_msg, nina_response, used_articles, escalated,
                      chat_messages=None, chatmail=False):
    """Schrijf één gespreksbeurt weg naar het externe weeklog (Google Sheet via Apps Script).

    Vercel bewaart zelf niks, dus we sturen elke beurt naar een Apps Script-webhook
    die een regel toevoegt aan een Google Sheet. Faalt altijd stil: als het log even
    niet bereikbaar is, merkt de bezoeker daar niets van en blijft Nina gewoon werken.
    We loggen GEEN IP of techniek — alleen vraag, antwoord en context.
    """
    log_url = os.getenv("NINA_LOG_URL")
    if not log_url:
        return
    artikelen = ", ".join(used_articles) if used_articles else "— geen artikel gevonden —"
    # De Sheet kent zes vaste kolommen; een zevende sleutel negeert de Apps Script.
    # Daarom staat de markering óók in de artikelkolom: die toont het weekoverzicht
    # al als "↳ bron: …", zonder dat Sheet of overzicht hoeven te veranderen.
    if chatmail:
        artikelen += CHATMAIL_LOGMARKERING
    try:
        _requests.post(
            log_url,
            json={
                "secret": os.getenv("NINA_LOG_SECRET", ""),
                "session_id": session_id or "",
                # Niet user_msg: dat is per definitie het LAATSTE bezoekersbericht en
                # bij een vastloper is dat de bevestiging ("Ja"), niet de vraag.
                "vraag": _inhoudelijke_vraag(chat_messages, user_msg) or (user_msg or ""),
                # Het kale laatste bericht gaat niet verloren; als de sheet deze kolom
                # (nog) niet kent, negeert de Apps Script hem.
                "laatste_bericht": user_msg or "",
                "antwoord": nina_response or "",
                "artikelen": artikelen,
                "escalatie": "ja" if escalated else "",
                "chatmail": "ja" if chatmail else "",
            },
            timeout=3,
        )
    except Exception as e:
        logger.warning(f"Weeklog-schrijven mislukt (genegeerd): {e}")


@app.get("/health")
async def health():
    return {"status": "ok"}
