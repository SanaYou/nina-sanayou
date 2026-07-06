#!/opt/homebrew/bin/python3
"""
Sync verboden woorden / stijlregels (bedrijfsbrede masterlijst) -> Nina's kennis.

Masterbron: ~/.flashbag/skills/social-agent/output/preflight.py (CHECKS + SPELL).
Dat is de bedrijfsbrede verboden-woordenlijst (ook Carrie draait erop). Nina op
Vercel kan die master niet live inladen, dus we genereren er een altijd-geladen
Nina-instructiebestand uit en pushen dat naar de nina-sanayou repo.

Canonieke locatie van het GEGENEREERDE bestand: nina-sanayou repo (= geback-upt).
Wordt nachtelijk aangeroepen door ~/.claude/sync-scripts/run-nina-articles.sh
(launchd com.sanayou.sync-nina-articles, 05:00).

- Alleen committen/pushen naar nina-sanayou als het bestand echt wijzigt.
- Met --no-push wordt alleen het bestand geschreven (voor lokale review vóór deploy).

Print een statusregel (de nachtelijke wrapper neemt die mee in het Telegram-bericht).
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

PREFLIGHT = Path("/Users/skarsten/.flashbag/skills/social-agent/output/preflight.py")
NINA_REPO = Path(__file__).resolve().parent.parent  # zelf-lokaliserend: de nina-chatbot repo
NINA_FILE = NINA_REPO / "knowledge" / "instructies" / "verboden-woorden.md"

HEADER = """# INTERN – Instructie Nina: Verboden en te vermijden woorden

## STATUS: ACTIEF

> AUTO-GEGENEREERD uit de bedrijfsbrede masterlijst (social-agent/preflight.py).
> NIET met de hand bewerken: wijzigingen horen in de masterlijst thuis en komen
> hier automatisch terecht via de nachtelijke sync (sync-stijl-to-nina.py).

Dit zijn de woorden en frases die SanaYou YOGAcademy bedrijfsbreed vermijdt. Ze
gelden ook voor jou, Nina, in elk antwoord. "Verboden" = nooit gebruiken.
"Vermijden" = alleen bewust en bij uitzondering.

"""

FOOTER = """
## Gerelateerde artikelen

- [Schrijfstijl en taalgebruik](schrijfstijl)
- [Kennisblogs zijn achtergrondinfo, niet ongevraagd uitstorten](kennisblogs-alleen-op-verzoek)
- [Anti-hallucinatie, scope, contact en boekbaarheid](scope-hallucinatie-escalatie)
"""


def load_preflight():
    spec = importlib.util.spec_from_file_location("preflight_master", PREFLIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_content(mod) -> str:
    verboden, vermijden = [], []
    for pat, level, msg in mod.CHECKS:
        # msg is al mens-leesbaar; we tonen alleen de boodschap, niet de regex.
        (verboden if level == "ERROR" else vermijden).append(msg)

    parts = [HEADER]
    parts.append("## Verboden (nooit gebruiken)\n")
    for m in verboden:
        parts.append(f"- {m}")
    parts.append("\n## Vermijden (alleen bewust gebruiken)\n")
    for m in vermijden:
        parts.append(f"- {m}")

    spell = getattr(mod, "SPELL", {})
    if spell:
        parts.append("\n## Veelgemaakte spel-/diacriticafouten\n")
        for fout, goed in spell.items():
            parts.append(f"- “{fout}” → “{goed}”")

    return "\n".join(parts).rstrip() + "\n" + FOOTER


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} faalde: {r.stderr.strip()}")
    return r.stdout.strip()


def main():
    no_push = "--no-push" in sys.argv
    mod = load_preflight()
    content = build_content(mod)

    old = NINA_FILE.read_text(encoding="utf-8") if NINA_FILE.exists() else ""
    if content == old:
        print("Verboden-woorden->Nina: ongewijzigd.")
        return

    NINA_FILE.write_text(content, encoding="utf-8")
    n = content.count("\n- ")
    if no_push:
        print(f"Verboden-woorden->Nina: bestand geschreven ({n} regels), niet gepusht (--no-push).")
        return

    rel = str(NINA_FILE.relative_to(NINA_REPO))
    git(NINA_REPO, "add", rel)
    git(NINA_REPO, "commit", "-q", "-m", "chore: sync verboden-woordenlijst naar Nina (auto)")
    git(NINA_REPO, "push", "--quiet")
    print(f"Verboden-woorden->Nina: bijgewerkt ({n} regels) + gepusht naar nina-sanayou.")


if __name__ == "__main__":
    main()
