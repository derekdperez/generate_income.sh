import httpx
import logging

class HttpService:
    def __init__(self, user_agent="BugBountyBot/1.0"):
        self.client = httpx.AsyncClient(headers={"User-Agent": user_agent}, verify=False)
        self.logger = logging.getLogger("HttpService")

    async def get(self, url, **Kwargs):
        self.logger.info(f"GET {url}")
        return await self.client.get(url, **kwargs)