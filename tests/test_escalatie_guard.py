"""Toetst dat de dubbele-escalatie-guard een BELOFTE niet aanziet voor een AFRONDING.

Aanleiding: Daphne, gesprek 13-9-2026 19:05. Zij vroeg zich af te melden voor haar
workshopdagen, gaf netjes naam en e-mailadres, en Nina sloot af met "Ik heb je verzoek
doorgestuurd naar Sandy". Er kwam nooit een Help Scout-ticket. Oorzaak: in de beurt
ervoor stond "Zodra ik dat heb, geef ik het door aan Sandy", en 'door aan sandy' zat in
het patroon waarmee de guard een eerdere afronding herkende. De guard onderdrukte dus de
enige echte escalatie van het gesprek.

Draait zonder pytest:  python3 tests/test_escalatie_guard.py
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


def escaleert(gesprek, laatste_antwoord, force=True):
    """Speelt _detect_and_escalate na en geeft terug of er een ticket zou ontstaan."""
    verstuurd = {}
    origineel = main._send_escalation
    main._send_escalation = lambda name, email, summary, msgs: verstuurd.update(
        name=name, email=email, summary=summary) or True
    try:
        main._detect_and_escalate(gesprek[-1]["content"] if gesprek else "",
                                  laatste_antwoord, gesprek, force=force)
    finally:
        main._send_escalation = origineel
    return bool(verstuurd)


print("\n1. Het echte gesprek van Daphne (13-9-2026) levert een ticket op")
DAPHNE = [
    u("Ik wil me afmelden voor alle workshop dagen hoe moet ik dat doen"),
    a("Hoi! Dat kan ik niet zelf voor je regelen in de administratie, maar ik zorg dat het "
      "bij Sandy terechtkomt zodat zij je kan afmelden.\n\nMag ik je naam en e-mailadres?"),
    u("Daphne Hartman, ik wil me afmelden voor alle workshop dagen waar ik me voor aan heb gemeld"),
    a("Even checken: je naam is Daphne Hartman, klopt dat? En wat is je e-mailadres?\n\n"
      "Zodra ik dat heb, geef ik het door aan Sandy zodat zij je afmeldt voor alle "
      "workshopdagen waarvoor je bent ingeschreven."),
    u("Daphnehartman@gmail.com"),
]
SLOT = ("Top, ik heb je gegevens genoteerd:\n\nNaam: Daphne Hartman\nE-mailadres: "
        "Daphnehartman@gmail.com\n\nIk heb je verzoek doorgestuurd naar Sandy.")
toets("belofte 'geef ik het door aan Sandy' blokkeert de escalatie niet",
      escaleert(DAPHNE, SLOT), True)

print("\n2. Een beurt waarin Nina nog om gegevens vraagt telt niet als afronding")
gesprek = [
    u("Ik wil mijn adres laten wijzigen"),
    a("Ik heb je naam genoteerd, wat is je e-mailadres?"),
    u("test@example.com"),
]
toets("'genoteerd' in een ask-beurt blokkeert de escalatie niet",
      escaleert(gesprek, "Dankjewel, ik heb het doorgestuurd naar Sandy."), True)

print("\n3. Een ECHTE eerdere afronding blokkeert een tweede ticket nog steeds")
gesprek = [
    u("Ik wil mijn adres laten wijzigen"),
    a("Even checken: je naam is Jans, je e-mailadres jans@example.com, klopt dat?"),
    u("ja klopt"),
    a("Top, ik heb het doorgestuurd naar Sandy. Ze neemt zo snel mogelijk contact met je op."),
    u("en kan ik ook een factuur krijgen?"),
]
toets("tweede afronding in hetzelfde gesprek wordt onderdrukt",
      escaleert(gesprek, "Ik heb het doorgegeven aan Sandy."), False)

print(f"\n{ok} geslaagd, {fout} gezakt")
sys.exit(1 if fout else 0)
