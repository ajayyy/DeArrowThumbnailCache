import json
from dataclasses import dataclass
import re
import requests
from utils.config import config
import time
import random
from utils.redis_handler import redis_conn
from typing import Any

def get_wait_period() -> int:
    return random.randint(15, 60) * 60

def fetch_proxies() -> list[Any]:
    if config["proxy_token"] is None:
        return []

    next_wait_period = float(redis_conn.get("next_proxy_fetch") or 0)
    last_fetch = float(redis_conn.get("last_proxy_fetch") or 0)
    if time.time() - last_fetch > next_wait_period:
        redis_conn.set("next_proxy_fetch", get_wait_period())
        redis_conn.set("last_proxy_fetch", time.time())

        response = requests.get(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100&ordering=-valid",
            headers={"Authorization": config["proxy_token"]}
        )

        result = response.json()
        if "results" in result:
            proxies = [result for result in result["results"] if result["valid"]]
            redis_conn.set("proxies", json.dumps(proxies))

            return proxies
        else:
            # Wait a minute for the rate limit to clear
            redis_conn.set("next_proxy_fetch", 60)

    return json.loads(redis_conn.get("proxies") or "[]")

def verify_proxy_url(url: str) -> bool:
    return re.match(r"^[0-9A-Za-z\/:@_%.]+$", url) is not None


@dataclass
class ProxyInfo:
    url: str
    country_code: str

def get_proxy_url() -> ProxyInfo | None:
    if config["proxy_token"] is None:
        return None

    proxies = fetch_proxies()

    if len(proxies) == 0:
        raise ValueError("No proxies available at the moment")
    else:
        chosen_proxy = proxies[random.randint(0, len(proxies) - 1)]
        url = f'http://{chosen_proxy["username"]}:{chosen_proxy["password"]}@{chosen_proxy["proxy_address"]}:{chosen_proxy["port"]}/'
        if verify_proxy_url(url):
            return ProxyInfo(url, chosen_proxy["country_code"])
        else:
            raise ValueError(f"Proxy url is invalid {url}")
