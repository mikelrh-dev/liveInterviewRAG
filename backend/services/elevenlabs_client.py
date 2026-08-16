"""ElevenLabs text-to-speech client (raw HTTP via httpx)."""

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"


class ElevenLabsError(Exception):
    """Raised when ElevenLabs synthesis fails (HTTP error, timeout, or IO)."""


class ElevenLabsClient:
    """Synthesize speech through the ElevenLabs text-to-speech API."""

    def __init__(self, api_key: str, voice_id: str, timeout: float = 15.0,
                 model_id: str = DEFAULT_MODEL_ID):
        self.api_key = api_key
        self.voice_id = voice_id
        self.timeout = timeout
        self.model_id = model_id

    async def synthesize(self, text: str, output_path: Path) -> Path:
        """Synthesize text to audio and write it to output_path.

        Args:
            text: Text to synthesize.
            output_path: Destination path for the generated audio file.

        Returns:
            The output path.

        Raises:
            ElevenLabsError: If the API returns an error status or the request fails.
        """
        url = f"{ELEVENLABS_API_URL}/{self.voice_id}"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {"text": text, "model_id": self.model_id}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise ElevenLabsError(
                    f"ElevenLabs API error {response.status_code}: {response.text[:200]}"
                )

            output_path.write_bytes(response.content)
            logger.info("Synthesized %d chars -> %s via ElevenLabs", len(text), output_path.name)
            return output_path

        except httpx.HTTPError as e:
            logger.error("ElevenLabs request failed: %s", e)
            raise ElevenLabsError(f"ElevenLabs request failed: {e}") from e
