import requests
from utils.config import config
import random

def get_proxy_url() -> str:
    if config["proxy_token"] is None:
        raise ValueError("Proxy token not set in config.yaml")

    response = requests.get(
        "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100&ordering=-valid",
        headers={"Authorization": config["proxy_token"]}
    )
    
    result = response.json()

    if "results" not in result:
        raise ValueError("Proxy API returned unexpected response")
    else:
        proxies = [result for result in result["results"] if result["valid"]]
        if len(proxies) == 0:
            raise ValueError("Proxy API returned no proxies")
        else:
            chosen_proxy = proxies[random.randint(0, len(proxies) - 1)]
            return f'http://{chosen_proxy["username"]}:{chosen_proxy["password"]}@{chosen_proxy["proxy_address"]}:{chosen_proxy["port"]}/'
