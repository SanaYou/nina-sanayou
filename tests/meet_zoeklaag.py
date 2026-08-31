#!/usr/bin/env python3
"""
Meetlat voor Nina's zoeklaag (retrieve_articles).

Waarom: op 30-8-2026 bleek dat een breed overzichtsartikel dat élke productnaam noemt
het specifieke lesdoelartikel uit de top 3 duwt. Raakcijfer over tien realistische
formuleringen: 6 van de 10. Korte vragen raakten, lange natuurlijke vragen niet.

Deze meetlat draait de ECHTE functies uit main.py — met ast eruit gelicht, zodat er geen
kopie van de scorelogica ontstaat die stilletjes uit de pas gaat lopen. fastapi is niet
nodig.

Draaien vanuit de repo-wortel:  python3 tests/meet_zoeklaag.py
"""
import ast
import re
import sys
from pathlib import Path
from typing import List

WORTEL = Path(__file__).resolve().parent.parent
NODIG_FUNCTIES = {"normalize", "expand_with_synonyms", "retrieve_articles",
                  "load_articles_index", "_extract_title", "_extract_collection",
                  "_extract_tags", "_bouw_woordgewichten", "_weegt", "_komt_voor"}
NODIG_NAMEN = {"SYNONIEMEN", "GEWICHT_ONBEKEND"}


def laad_zoeklaag():
    """Licht de scorefuncties uit main.py zonder de app te importeren."""
    boom = ast.parse((WORTEL / "main.py").read_text(encoding="utf-8"))
    stukken = []
    for knoop in boom.body:
        if isinstance(knoop, ast.FunctionDef) and knoop.name in NODIG_FUNCTIES:
            stukken.append(knoop)
        elif isinstance(knoop, ast.Assign):
            doelen = {t.id for t in knoop.targets if isinstance(t, ast.Name)}
            if doelen & NODIG_NAMEN:
                stukken.append(knoop)
    gevonden = {k.name for k in stukken if isinstance(k, ast.FunctionDef)}
    ontbreekt = NODIG_FUNCTIES - gevonden
    if ontbreekt:
        raise SystemExit(f"Niet gevonden in main.py: {sorted(ontbreekt)} — is de zoeklaag hernoemd?")
    ruimte = {"re": re, "Path": Path, "List": List, "sys": sys}
    exec(compile(ast.Module(body=stukken, type_ignores=[]), "main.py", "exec"), ruimte)
    return ruimte


# (vraag, bestandsnaam van het artikel dat bovenaan hoort te staan)
# Vijf kort, vijf lang en natuurlijk geformuleerd — precies de verdeling waarop de
# meting van 30-8 struikelde.
PROEFVRAGEN = [
    ("lesdoelen Yin Yoga Basics", "lesdoelen-yin-yoga-basics"),
    ("lesdoelen Yoga Nidra", "lesdoelen-yoga-nidra"),
    ("lesdoelen Ashtanga Yoga", "lesdoelen-ashtanga-yoga"),
    ("lesdoelen Yang Yoga Level 1", "lesdoelen-yang-yoga-level-1"),
    ("lesdoelen nervus vagus", "lesdoelen-de-kracht-van-de-nervus-vagus"),
    ("wat leer ik precies in de module Yoga Nidra?", "lesdoelen-yoga-nidra"),
    ("wat kan ik na het volgen van de module over de nervus vagus?",
     "lesdoelen-de-kracht-van-de-nervus-vagus"),
    ("welke onderwerpen komen aan bod bij Yin Yoga en de wereld van fascia?",
     "lesdoelen-yin-yoga-en-de-wereld-van-fascia"),
    ("ik wil graag weten wat de leerdoelen zijn van Critical Alignment Yoga",
     "lesdoelen-critical-alignment-yoga"),
    ("kun je me vertellen wat ik leer tijdens Yin Yoga 2?", "lesdoelen-yin-yoga-2"),
]


def meet(toon=True):
    ruimte = laad_zoeklaag()
    index = ruimte["load_articles_index"]()
    ruimte["ARTICLES_INDEX"] = index
    # de zeldzaamheidsgewichten horen bij DEZE index, net als in main.py bij het opstarten
    ruimte["WOORDGEWICHTEN"] = ruimte["_bouw_woordgewichten"](index)
    op_id = {a["id"]: a["title"] for a in index}
    ontbrekend = [v for _, v in PROEFVRAGEN if v not in op_id]
    if ontbrekend:
        raise SystemExit(f"Proefvraag wijst naar een artikel dat niet bestaat: {ontbrekend}")

    raak = 0
    regels = []
    for vraag, verwacht_id in PROEFVRAGEN:
        # ⚠️ retrieve_articles geeft een TUPLE (kennistekst, [titels]) terug.
        _tekst, titels = ruimte["retrieve_articles"](vraag, [])
        verwachte_titel = op_id[verwacht_id]
        gevonden = verwachte_titel in titels
        raak += gevonden
        regels.append((gevonden, vraag, verwachte_titel, titels))

    if toon:
        for gevonden, vraag, verwacht, titels in regels:
            print(f"{'✅' if gevonden else '❌'} {vraag}")
            if not gevonden:
                print(f"     hoort: {verwacht}")
                for t in titels:
                    print(f"     kreeg: {t}")
        print(f"\nRAAKCIJFER: {raak}/{len(PROEFVRAGEN)}")
    return raak, len(PROEFVRAGEN), regels


if __name__ == "__main__":
    raak, totaal, _ = meet()
    sys.exit(0 if raak == totaal else 1)


# ── Terugvalproef ─────────────────────────────────────────────────────────────
# "Conclusie nooit breder dan de meting": de tien vragen hierboven zijn de vragen
# waaróp gerepareerd is. Deze set toetst de andere kant op — blijven de gewone
# klantvragen (prijs, annuleren, certificaat, examen, praktisch) hetzelfde antwoord
# vinden? Een zoeklaag die lesdoelen beter vindt maar annuleringsvoorwaarden kwijtraakt,
# is geen verbetering.
TERUGVALVRAGEN = [
    ("hoe kan ik mijn abonnement opzeggen?", "abonnement-opzeggen"),
    ("wat zijn de annuleringsvoorwaarden voor een workshop?", "annuleringsvoorwaarden-workshops"),
    ("krijg ik een diploma na het afronden van de RYT200 online?", "diploma-na-afronding-ryt200-online"),
    ("ik wil een factuur van mijn online module", "factuur-online-module"),
    ("hoe log ik in op Huddle?", "inloggen-huddle"),
    ("wat is de klachtenprocedure?", "klachtenprocedure"),
    ("hoe groot zijn de groepen bij de klassikale opleiding?", "groepsgrootte-klassikale-opleiding"),
    ("mag ik het lesmateriaal delen met anderen?", "lesmateriaal-delen"),
    ("hoeveel tijd kost een online scholing ongeveer?", "hoeveel-tijd-kost-een-online-scholing"),
    ("wat gebeurt er als ik een lesdag mis bij de RYT200?", "lesdagen-missen-ryt200"),
    ("is er een leeftijdsgrens voor de klassikale RYT200?", "leeftijdsgrens-klassikale-ryt200"),
    ("kan ik in mijn eigen tempo studeren?", "eigen-tempo-studeren"),
]


def meet_set(ruimte, index, vragen):
    op_id = {a["id"]: a["title"] for a in index}
    ontbrekend = [v for _, v in vragen if v not in op_id]
    if ontbrekend:
        raise SystemExit(f"Proefvraag wijst naar een artikel dat niet bestaat: {ontbrekend}")
    uitslag = []
    for vraag, verwacht_id in vragen:
        _tekst, titels = ruimte["retrieve_articles"](vraag, [])
        uitslag.append((op_id[verwacht_id] in titels, vraag, op_id[verwacht_id], titels))
    return uitslag
