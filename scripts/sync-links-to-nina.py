#!/opt/homebrew/bin/python3
"""
Sync checkout-/aanmeldlinks vanuit de vault-referentie -> Nina's linklijst.

Waarom: Nina draait in de cloud (Vercel) en kan de vault NIET lezen. Ze heeft haar
eigen `knowledge/aanmeldlinks-en-anchors.md` (die tegelijk haar checkout-allowlist
is). Die liep uit de pas met de vault: een geverifieerde link stond wél in de vault
maar niet bij Nina -> bezoeker liep dood (12-7-2026). Deze sync maakt de vault de
ENE bron van waarheid: elke checkout-link in de vault staat de volgende ochtend ook
bij Nina.

Werking (veilig + additief):
- Leest alle `https://checkout.sanayou.com/checkout/...`-links (+ label) uit de vault.
- Laat het handmatig onderhouden deel van het anchors-bestand ONGEMOEID.
- (Her)bouwt één gemarkeerd AUTO-blok met de links die nog NIET ergens in het
  handmatige deel staan. Verwijdert nooit iets van de hand-curatie.
- Committen/pushen naar nina-sanayou alleen als het bestand echt wijzigt.

Nachtelijk aangeroepen (zie run-wrapper / launchd). Handmatig: `--dry-run` toont
wat er zou veranderen zonder te schrijven of pushen.
"""
import re
import subprocess
import sys
from pathlib import Path

VAULT_REF = Path.home() / "common-ground" / "vault" / "references" / "sanayou-reference.md"
NINA_REPO = Path(__file__).resolve().parent.parent
ANCHORS = NINA_REPO / "knowledge" / "aanmeldlinks-en-anchors.md"

START = "<!-- AUTO-SYNC-VAULT-LINKS:START (niet handmatig bewerken — gesynct uit vault) -->"
END = "<!-- AUTO-SYNC-VAULT-LINKS:END -->"
URL_RE = re.compile(r"https?://checkout\.sanayou\.com/checkout/\S+")


def _clean_url(u: str) -> str:
    return u.rstrip("`).,;")


GENERIEK = {"", "boeken", "aanmelden", "checkout", "link", "hier", "aanvragen", "url"}


def _slug_label(url: str) -> str:
    """Leesbaar label afgeleid uit de URL-slug (fallback)."""
    slug = url.rsplit("/checkout/", 1)[-1]
    slug = re.sub(r"-?\d{4,}$", "", slug)          # trailing jaar/id eraf
    slug = slug.replace("-", " ").strip()
    return (slug[:1].upper() + slug[1:]) if slug else "Checkout-link"


def _label_for(line: str, url: str) -> str:
    """Best-effort label: tekst vóór de URL op dezelfde regel, opgeschoond.
    Is die generiek/leeg (bv. '**Boeken:**'), val terug op de URL-slug."""
    before = line.split(url)[0]
    before = before.strip().strip("|").strip()
    before = re.sub(r"^[-*]\s*", "", before)
    before = before.replace("`", "").replace("*", "").strip()
    before = re.sub(r"(?i)\b(aanvragen|link|checkout)\s*:?\s*$", "", before).strip(" :-")
    if before.lower().strip(" :-") in GENERIEK or len(before) < 3:
        return _slug_label(url)
    return before


def links_from_vault() -> dict:
    """{url: label} voor alle checkout-links in de vault-referentie."""
    out = {}
    if not VAULT_REF.exists():
        print(f"[sync-links] vault-referentie niet gevonden: {VAULT_REF}", file=sys.stderr)
        return out
    for line in VAULT_REF.read_text(encoding="utf-8").splitlines():
        for m in URL_RE.finditer(line):
            url = _clean_url(m.group(0))
            out.setdefault(url, _label_for(line, m.group(0)))
    return out


def split_managed(text: str):
    """Retourneer (handmatig_deel, ) — het bestaande AUTO-blok wordt eruit gehaald."""
    if START in text and END in text:
        pre, rest = text.split(START, 1)
        _, post = rest.split(END, 1)
        return (pre.rstrip() + "\n" + post.lstrip()).rstrip() + "\n"
    return text.rstrip() + "\n"


def build(dry_run: bool = False) -> bool:
    vault_links = links_from_vault()
    if not vault_links:
        print("[sync-links] geen links uit vault — niets te doen")
        return False

    huidig = ANCHORS.read_text(encoding="utf-8")
    handmatig = split_managed(huidig)

    # Alleen links toevoegen die nog NIET in het handmatige deel staan.
    aanwezig = {_clean_url(m.group(0)) for m in URL_RE.finditer(handmatig)}
    nieuw = {u: l for u, l in vault_links.items() if u not in aanwezig}

    if nieuw:
        regels = "\n".join(f"- {label}: `{url}`" for url, label in sorted(nieuw.items(), key=lambda x: x[1].lower()))
        blok = (f"{START}\n\n"
                "## Automatisch gesynct uit de vault-referentie\n\n"
                "_Deze links komen uit `vault/references/sanayou-reference.md`. Niet met de hand "
                "bewerken — pas ze in de vault aan, dan volgt Nina vanzelf._\n\n"
                f"{regels}\n\n{END}\n")
        nieuw_bestand = handmatig.rstrip() + "\n\n" + blok
    else:
        nieuw_bestand = handmatig

    if nieuw_bestand == huidig:
        print(f"[sync-links] geen wijziging ({len(vault_links)} vault-links, alles al aanwezig)")
        return False

    print(f"[sync-links] {len(nieuw)} nieuwe link(s) toe te voegen aan Nina:")
    for url, label in sorted(nieuw.items(), key=lambda x: x[1].lower()):
        print(f"    + {label}: {url}")

    if dry_run:
        print("[sync-links] --dry-run: niets geschreven/gepusht")
        return True

    ANCHORS.write_text(nieuw_bestand, encoding="utf-8")
    try:
        subprocess.run(["git", "-C", str(NINA_REPO), "add", str(ANCHORS)], check=True)
        subprocess.run(["git", "-C", str(NINA_REPO), "commit", "-m",
                        f"Sync aanmeldlinks uit vault ({len(nieuw)} nieuw)"], check=True)
        subprocess.run(["git", "-C", str(NINA_REPO), "push"], check=True)
        print(f"[sync-links] {len(nieuw)} link(s) gecommit + gepusht -> Vercel deployt")
    except subprocess.CalledProcessError as e:
        print(f"[sync-links] git-fout: {e}", file=sys.stderr)
        sys.exit(1)
    return True


if __name__ == "__main__":
    build(dry_run="--dry-run" in sys.argv)
