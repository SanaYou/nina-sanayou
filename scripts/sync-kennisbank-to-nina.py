#!/opt/homebrew/bin/python3
"""
Sync kennisbank long-reads (sanayou-pages) -> Nina's kennisbron.

Canonieke locatie: nina-sanayou repo (wordt gepusht = duurzaam geback-upt).
Wordt nachtelijk aangeroepen door ~/.claude/sync-scripts/run-nina-articles.sh
(launchd com.sanayou.sync-nina-articles, 05:00).

Regenereert knowledge/kennisbank-artikelen.md uit de gepubliceerde kennisbank-
artikelen van sanayou-pages (gelezen van origin/main, zodat ook artikelen die
elders/in de cloud zijn gepusht worden meegenomen).

- Bestaande, met de hand verfijnde "Kern:"-teksten blijven behouden (op slug).
- Nieuwe artikelen krijgen hun excerpt als kern (gezondheidsartikelen + medische noot).
- Alleen committen/pushen naar nina-sanayou als het bestand echt wijzigt.

Print een statusregel (de nachtelijke wrapper neemt die mee in het Telegram-bericht).
"""
import re
import subprocess
import sys
from pathlib import Path

SANAYOU_REPO = Path("/Users/skarsten/sanayou-pages")
NINA_REPO = Path(__file__).resolve().parent.parent  # zelf-lokaliserend: de nina-chatbot repo
NINA_FILE = NINA_REPO / "knowledge" / "kennisbank-artikelen.md"
SITE = "https://sanayou.com"

# Kennisbank-categorieën (zelfde set als build.js) + nette kop + volgorde
CATEGORIES = [
    ("yogadocent-worden", "Yogadocent worden"),
    ("yogastijlen", "Yogastijlen"),
    ("yogakennis", "Yogakennis en verdieping"),
    ("yoga-en-gezondheid", "Yoga en gezondheid"),
    ("yogahoudingen", "Yogahoudingen"),
]
HEALTH_CAT = "yoga-en-gezondheid"
HEALTH_NOTE = " Voeg er altijd bij dat yoga geen medische behandeling vervangt."

HEADER = """# Kennisbank-artikelen (long-reads op de website)

Dit zijn uitgebreide achtergrondartikelen op de SanaYou-website (de Kennisbank). Gebruik de korte kern hieronder om vragen te beantwoorden, en verwijs voor de volledige uitleg naar het bijbehorende artikel. Noem de titel en geef de link wanneer iemand meer diepgang of context wil.

Belangrijk:
- Voor prijzen, tarieven en operationele details blijven je gewone helpartikelen leidend. Deze long-reads zijn voor verdieping en context, niet voor exacte bedragen.
- Verzin nooit een link die hier niet staat.
- Voor persoonlijk studieadvies blijf je zelf het contactpunt via de chat.
"""


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} faalde: {r.stderr.strip()}")
    return r.stdout


def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        mm = re.match(r'^(\w+):\s*"?(.*?)"?\s*$', line)
        if mm:
            meta[mm.group(1)] = mm.group(2)
    return meta


def load_existing_kerns():
    """slug -> bestaande Kern-tekst, zodat handmatige verfijning behouden blijft."""
    kerns = {}
    if not NINA_FILE.exists():
        return kerns
    text = NINA_FILE.read_text(encoding="utf-8")
    # blokken: ### titel \n Link: <url> \n Kern: <kern>
    for m in re.finditer(r"Link:\s*\S*/blog/([a-z0-9-]+)/\s*\nKern:\s*(.+)", text):
        kerns[m.group(1)] = m.group(2).strip()
    return kerns


def main():
    # 1. Haal de laatste gepubliceerde content op (zonder de working tree te raken)
    git(SANAYOU_REPO, "fetch", "--quiet", "origin", "main")
    tree = git(SANAYOU_REPO, "ls-tree", "-r", "--name-only", "origin/main", "content/blog/")

    existing_kerns = load_existing_kerns()

    # 2. Verzamel gepubliceerde artikelen per categorie
    by_cat = {slug: [] for slug, _ in CATEGORIES}
    cat_slugs = {slug for slug, _ in CATEGORIES}
    for path in tree.splitlines():
        if not path.endswith(".md"):
            continue
        parts = path.split("/")
        if len(parts) != 4:  # content/blog/<cat>/<file>.md
            continue
        cat = parts[2]
        if cat not in cat_slugs:
            continue
        raw = git(SANAYOU_REPO, "show", f"origin/main:{path}")
        meta = parse_frontmatter(raw)
        if meta.get("status") and meta["status"] != "published":
            continue
        if not meta.get("slug") or not meta.get("title"):
            continue
        by_cat[cat].append(meta)

    # 3. Bouw het bestand
    blocks = [HEADER]
    total = 0
    for cat, heading in CATEGORIES:
        arts = sorted(by_cat[cat], key=lambda m: m.get("date", ""))
        if not arts:
            continue
        blocks.append(f"## {heading}\n")
        for meta in arts:
            slug = meta["slug"]
            title = meta["title"]
            url = f"{SITE}/blog/{slug}/"
            kern = existing_kerns.get(slug)
            if not kern:
                kern = meta.get("excerpt", "").strip()
                if cat == HEALTH_CAT and "medische" not in kern.lower():
                    kern = kern.rstrip(".") + "." + HEALTH_NOTE
            blocks.append(f"### {title}\nLink: {url}\nKern: {kern}\n")
            total += 1

    new_content = "\n".join(blocks).rstrip() + "\n"

    # 4. Alleen schrijven/pushen bij wijziging
    old_content = NINA_FILE.read_text(encoding="utf-8") if NINA_FILE.exists() else ""
    if new_content == old_content:
        print(f"Kennisbank->Nina: geen wijzigingen ({total} artikelen).")
        return 0

    NINA_FILE.write_text(new_content, encoding="utf-8")
    git(NINA_REPO, "add", str(NINA_FILE.relative_to(NINA_REPO)))
    git(NINA_REPO, "commit", "-q", "-m",
        f"Nina: kennisbank-long-reads gesynct ({total} artikelen) [auto]\n\n"
        "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>")
    git(NINA_REPO, "push", "--quiet")
    print(f"Kennisbank->Nina: bijgewerkt naar {total} artikelen + gepusht naar nina-sanayou.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Kennisbank->Nina: FOUT - {e}")
        sys.exit(1)
