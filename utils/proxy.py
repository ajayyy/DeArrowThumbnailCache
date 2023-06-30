import requests
from utils.config import config
import time
import random

def get_wait_period() -> int:
    return random.randint(15, 60) * 60

proxies = []
last_fetch = 0
next_wait_period = get_wait_period()

def fetch_proxies():
    global proxies, next_wait_period, last_fetch

    if config["proxy_token"] is None:
        raise ValueError("Proxy token not set in config.yaml")
    
    if time.time() - last_fetch > next_wait_period:
        next_wait_period = get_wait_period()
        last_fetch = time.time()

        response = requests.get(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100&ordering=-valid",
            headers={"Authorization": config["proxy_token"]}
        )
        
        result = response.json()
        if "results" in result:
            proxies = [result for result in result["results"] if result["valid"]]
        else:
            # Wait at least a minute for the rate limit to clear
            next_wait_period = random.randint(60, 90)


def get_proxy_url() -> str:
    if config["proxy_token"] is None:
        raise ValueError("Proxy token not set in config.yaml")

    fetch_proxies()

    if len(proxies) == 0:
        raise ValueError("No proxies available at the moment")
    else:
        chosen_proxy = proxies[random.randint(0, len(proxies) - 1)]
        return f'http://{chosen_proxy["username"]}:{chosen_proxy["password"]}@{chosen_proxy["proxy_address"]}:{chosen_proxy["port"]}/'
