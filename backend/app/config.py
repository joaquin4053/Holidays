"""Settings, read from the project-root .env."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
# Tried in this order when the main model is down or limited, before Groq (comma separated, free NVIDIA only).
OPENROUTER_BACKUP_MODELS = [m.strip() for m in os.getenv("OPENROUTER_BACKUP_MODELS",
                                                         "nvidia/nemotron-3.5-super-vl:free").split(",") if m.strip()]

# Fallback when OpenRouter is rate-limited: Groq free tier (30 requests/min, 1,000/day, 8,000 tokens/min).
# The model is fixed on purpose (user requirement: free models only).
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_TOKENS_PER_MINUTE = 8000
GROQ_REQUESTS_PER_MINUTE = 30

# Free OpenRouter models: 20 requests/minute, 50/day (1000/day after buying >= 10 USD credits).
LLM_CONCURRENCY = 2
SEARCH_RESULTS_PER_QUERY = 5
# Live prices from booking sites via a local headless browser (free). LIVE_PRICES=0 in .env turns it off.
LIVE_PRICES = os.getenv("LIVE_PRICES", "1") != "0"
# Read caches (writing always happens). 0 = measure cold runs (tools/perf_run.py); Tavily cold costs credits.
LIVE_CACHE = os.getenv("LIVE_CACHE", "1") != "0"
TAVILY_CACHE = os.getenv("TAVILY_CACHE", "1") != "0"
LLM_CACHE = os.getenv("LLM_CACHE", "1") != "0"  # same prompt = stored answer, no new request
# 1 = one LLM request per trip (fewest requests, ~45 s for Rome). 0 = one per place/leg + day plan in
# parallel (4 requests for Rome, ~27 s): faster because the answers are written at the same time.
LLM_SINGLE_REQUEST = os.getenv("LLM_SINGLE_REQUEST", "1") != "0"

TRIPS_DIR = ROOT / ".tmp" / "trips"


def setup_logging() -> None:
    """App logs (which LLM provider answered, blocked live pages, rate limits) on stderr."""
    log = logging.getLogger("holydays")
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False


def check_free_model(model: str) -> None:
    """Hard lock: only free NVIDIA models may ever be called (user requirement)."""
    if not (model.startswith("nvidia/") and model.endswith(":free")):
        raise RuntimeError(
            f"OPENROUTER_MODEL={model!r} is not allowed: only free NVIDIA models "
            "('nvidia/...:free') may be used."
        )


for _m in (OPENROUTER_MODEL, *OPENROUTER_BACKUP_MODELS):
    check_free_model(_m)
