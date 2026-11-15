"""One small client for any OpenAI-compatible chat endpoint. Off unless LLM_PROVIDER is set.

Environment (or a .env file in the working directory):
    LLM_PROVIDER  none | groq | openrouter | ollama
    LLM_MODEL     model id as listed by the provider's /models
    LLM_API_KEY   not needed for ollama
    LLM_BASE_URL  overrides the provider's default address
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config


def settings() -> dict:
    env = dict(os.environ)
    dotenv = Path(".env")
    if dotenv.is_file():  # real environment variables win over .env
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                env.setdefault(key.strip(), value.strip().strip("\"'"))
    provider = env.get("LLM_PROVIDER", "none").strip().lower() or "none"
    return {"provider": provider,
            "base_url": (env.get("LLM_BASE_URL") or config.LLM_PROVIDERS.get(provider, "")).rstrip("/"),
            "model": env.get("LLM_MODEL") or None,
            "api_key": env.get("LLM_API_KEY", "")}


def enabled(cfg=None) -> bool:
    cfg = cfg or settings()
    return (cfg["provider"] != "none" and bool(cfg["model"])
            and cfg["base_url"].startswith(("http://", "https://")))


def _parse(content):
    """The model's reply as a dict, or None. Tolerates ```json fences around it."""
    try:
        data = json.loads(content[content.index("{"):content.rindex("}") + 1])
    except (ValueError, TypeError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def _post(cfg, body):
    request = urllib.request.Request(
        cfg["base_url"] + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['api_key']}",
                 "User-Agent": "dxf2ifc/0.1"})  # some providers reject urllib's default agent
    with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT) as response:
        return json.loads(response.read())["choices"][0]["message"]["content"]


def chat_json(system: str, user: str, cache_dir=None) -> tuple[dict | None, bool]:
    """Ask once and return (answer as dict or None, came from cache). Never raises."""
    cfg = settings()
    if not enabled(cfg):
        return None, False
    cache_dir = Path(cache_dir or config.CACHE_DIR) / "llm"
    key = hashlib.sha1("\n".join([cfg["model"], system, user]).encode("utf-8")).hexdigest()
    cache_file = cache_dir / f"{key}.json"
    if cache_file.is_file():
        return json.loads(cache_file.read_text(encoding="utf-8")), True

    body = {"model": cfg["model"], "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    answer, waited = None, False
    for _ in range(3):
        try:
            answer = _parse(_post(cfg, body))
            break
        except urllib.error.HTTPError as err:
            if err.code == 429 and not waited:  # rate limit: wait once
                waited = True
                time.sleep(config.LLM_RETRY_WAIT)
            elif err.code == 400 and "response_format" in body:  # model without JSON mode
                del body["response_format"]
            else:
                break
        except Exception:  # timeout, no network, odd reply shape: carry on without the LLM
            break
    if answer is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(answer), encoding="utf-8")
    return answer, False
