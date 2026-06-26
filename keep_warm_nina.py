"""
Nina Keep-Warm: ping /health elke 10 minuten zodat Vercel de functie warm houdt.
Voorkomt cold-starts (20-40s) voor de eerste klant op /chat.
Geen alerts — dat is het werk van monitor_nina.py.
"""
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nina-keep-warm")

NINA_URL = "https://nina-chatbot.vercel.app"


def ping():
    try:
        r = requests.get(f"{NINA_URL}/health", timeout=30)
        logger.info(f"ping status={r.status_code} duration={r.elapsed.total_seconds():.2f}s")
    except Exception as e:
        logger.warning(f"keep-warm ping mislukt (geen alert, monitor doet dat): {e}")


if __name__ == "__main__":
    ping()
