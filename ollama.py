"""Ollama API client for Smart Heating Advisor."""
import json
import logging
import aiohttp

_LOGGER = logging.getLogger(__name__)


class OllamaClient:
    """Client for communicating with Ollama API."""

    def __init__(self, url: str, model: str, timeout: int = 120):
        """Initialize Ollama client."""
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def async_generate(self, prompt: str) -> str | None:
        """Send prompt to Ollama and return response text."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,  # Low temperature for consistent JSON output
                "num_predict": 256,  # Limit response length
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        _LOGGER.error(
                            "Ollama returned status %s: %s",
                            response.status,
                            await response.text()
                        )
                        return None

                    data = await response.json()
                    return data.get("response")

        except aiohttp.ClientConnectorError:
            _LOGGER.error("Cannot connect to Ollama at %s", self.url)
            return None
        except TimeoutError:
            _LOGGER.error("Ollama request timed out after %s seconds", self.timeout)
            return None
        except Exception as e:
            _LOGGER.error("Ollama request failed: %s", e)
            return None

    async def async_parse_json_response(self, response: str) -> dict | None:
        """Parse JSON from Ollama response safely."""
        if not response:
            return None
        try:
            # Strip any markdown code fences if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except json.JSONDecodeError as e:
            _LOGGER.error("Failed to parse Ollama JSON response: %s\nResponse: %s", e, response)
            return None

    async def async_test_connection(self) -> bool:
        """Test if Ollama is reachable and model is available."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        return False
                    data = await response.json()
                    models = [m["name"] for m in data.get("models", [])]
                    available = any(self.model in m for m in models)
                    if not available:
                        _LOGGER.warning(
                            "Model %s not found in Ollama. Available: %s",
                            self.model,
                            models
                        )
                    return available
        except Exception as e:
            _LOGGER.error("Ollama connection test failed: %s", e)
            return False