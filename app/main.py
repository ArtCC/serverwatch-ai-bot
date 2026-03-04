import json
import logging
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(
    level=getattr(logging, os.getenv("BOT_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("serverwatch")


def read_json(url: str, timeout: int = 10) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload)


def check_glances(base_url: str) -> None:
    now_url = f"{base_url.rstrip('/')}/quicklook"
    payload = read_json(now_url)
    cpu = payload.get("cpu")
    mem = payload.get("mem")
    logger.info("Glances OK | cpu=%s | mem=%s", cpu, mem)


def check_ollama(base_url: str) -> None:
    tags_url = f"{base_url.rstrip('/')}/api/tags"
    payload = read_json(tags_url)
    models_count = len(payload.get("models", []))
    logger.info("Ollama OK | models=%s", models_count)


def main() -> None:
    glances_url = os.getenv("GLANCES_BASE_URL", "http://glances:61208/api/4")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    interval = int(os.getenv("BOOTSTRAP_CHECK_INTERVAL_SECONDS", "60"))

    logger.info("ServerWatch bootstrap started")
    logger.info("GLANCES_BASE_URL=%s", glances_url)
    logger.info("OLLAMA_BASE_URL=%s", ollama_url)

    while True:
        try:
            check_glances(glances_url)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Glances check failed: %s", error)

        try:
            check_ollama(ollama_url)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Ollama check failed: %s", error)

        time.sleep(interval)


if __name__ == "__main__":
    main()
