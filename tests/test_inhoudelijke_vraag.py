"""Toetst dat het weeklog en het escalatie-onderwerp de VRAAG loggen, niet de "Ja".

Aanleiding: Olivia mat op 1-9-2026 dat alle 5 vastlopers van augustus als vraag
letterlijk een bevestiging droegen ("Ja", "ja helemaal juist", "Ja klopt", "Ja dat
klopt", "Ja dat klopt") en dat twee Help Scout-tickets "Nina-escalatie: Ja" heetten.

Draait zonder pytest en zonder externe pakketten:  python3 tests/test_inhoudelijke_vraag.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.test_opstart  # noqa: F401  — zet de nep-pakketten klaar en importeert main
import main

ok, fout = 0, 0


def toets(omschrijving, gekregen, verwacht):
    global ok, fout
    if gekregen == verwacht:
        ok += 1
        print(f"  ✅ {omschrijving}")
    else:
        fout += 1
        print(f"  ❌ {omschrijving}\n       verwacht: {verwacht!r}\n       gekregen: {gekregen!r}")


def u(t): return {"role": "user", "content": t}
def a(t): return {"role": "assistant", "content": t}


print("\n1. De vijf gemeten vastlopers van augustus (Olivia, 1-9-2026)")
VRAAG = "Kan ik de opleiding ook in termijnen betalen?"
for bevestiging in ["Ja", "ja helemaal juist", "Ja klopt", "Ja dat klopt", "ja"]:
    gesprek = [u(VRAAG), a("Bedoel je de RYT/VYN200 klassikaal?"), u(bevestiging)]
    toets(f"laatste bericht {bevestiging!r} -> logt de vraag",
          main._inhoudelijke_vraag(gesprek, bevestiging), VRAAG)

print("\n2. Kale bevestigingen worden herkend, echte vragen NIET")
for t in ["Ja", "ja klopt", "Ja dat klopt", "ja helemaal juist", "nee", "ok", "top",
          "bedankt", "Dank je wel", "", "   "]:
    toets(f"{t!r} is een kale bevestiging", main._is_kale_bevestiging(t), True)
for t in ["Ja, maar kan ik ook in termijnen betalen?", "nee ik bedoel de online opleiding",
          "Wat kost de module Yin Yoga 2?", "ok en hoe zit het met de examendatum",
          "ja hoor, wanneer start de volgende groep in Zwolle?"]:
    toets(f"{t!r} is GEEN kale bevestiging", main._is_kale_bevestiging(t), False)

print("\n3. Terugvallen als er niets inhoudelijks is")
toets("alleen bevestigingen -> eerste bezoekersbericht",
      main._inhoudelijke_vraag([u("ja"), a("?"), u("ok")], "ok"), "ja")
toets("leeg gesprek -> het laatste bericht zelf",
      main._inhoudelijke_vraag([], "Ja"), "Ja")
toets("geen enkel bericht -> lege string",
      main._inhoudelijke_vraag([], ""), "")

print("\n4. De samenvouw-lus uit /chat (cleaned[-1] = msg) — dit at de openingsvraag op")
# twee opeenvolgende user-berichten worden in /chat samengevouwen tot het LAATSTE
rauw = [u(VRAAG), u("Ja")]
cleaned = []
for m in rauw:
    if cleaned and cleaned[-1]["role"] == m["role"]:
        cleaned[-1] = m
    else:
        cleaned.append(m)
toets("de opschoonlus houdt inderdaad alleen 'Ja' over (de oorzaak)",
      [m["content"] for m in cleaned], ["Ja"])
toets("en tóch logt de nieuwe regel geen 'Ja' als de vraag meekomt",
      main._inhoudelijke_vraag(cleaned + [u(VRAAG), a("?")], "Ja"), VRAAG)

print("\n5. Het escalatie-onderwerp gebruikt dezelfde regel")
verstuurd = {}
main._hs_get_token = lambda: "nep-token"
main._send_escalation = lambda name, email, summary, msgs: verstuurd.update(summary=summary)
gesprek = [u(VRAAG), a("Mag ik je e-mailadres?"), u("sandy@example.com"),
           a("Klopt dit?"), u("Ja")]
main._detect_and_escalate("Ja", "Ik zet dit door naar een collega.", gesprek, force=True)
toets("onderwerp is de vraag, niet 'Ja'", verstuurd.get("summary"), VRAAG)
toets("onderwerp bevat nooit meer letterlijk 'Ja'", verstuurd.get("summary") == "Ja", False)

print("\n6. Een e-mailadres wordt nooit het onderwerp")
toets("mailadres als laatste inhoudelijke bericht -> valt terug",
      "@" in (main._inhoudelijke_vraag([u(VRAAG), u("sandy@example.com")],
                                       "sandy@example.com") or ""), True)

print(f"\n{'='*60}\n  {ok} geslaagd, {fout} gefaald\n{'='*60}")
sys.exit(1 if fout else 0)
