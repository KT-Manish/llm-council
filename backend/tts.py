"""Text-to-Speech using OpenAI TTS API."""

import httpx
from typing import AsyncGenerator, Optional


async def text_to_speech_stream(
    api_key: str,
    text: str,
    voice: str = "alloy",
    model: str = "tts-1",
    response_format: str = "mp3"
) -> AsyncGenerator[bytes, None]:
    """Stream text-to-speech audio from OpenAI API.

    Args:
        api_key: OpenAI API key
        text: Text to convert to speech
        voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
        model: TTS model (tts-1 or tts-1-hd)
        response_format: Audio format (mp3, opus, aac, flac, wav, pcm)

    Yields:
        Audio data chunks
    """
    url = "https://api.openai.com/v1/audio/speech"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format
    }

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=60.0
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise Exception(f"TTS API error: {response.status_code} - {error_text}")

            async for chunk in response.aiter_bytes(chunk_size=4096):
                yield chunk


async def text_to_speech(
    api_key: str,
    text: str,
    voice: str = "alloy",
    model: str = "tts-1",
    response_format: str = "mp3"
) -> Optional[bytes]:
    """Convert text to speech and return complete audio.

    Args:
        api_key: OpenAI API key
        text: Text to convert to speech
        voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
        model: TTS model (tts-1 or tts-1-hd)
        response_format: Audio format (mp3, opus, aac, flac, wav, pcm)

    Returns:
        Complete audio data or None on error
    """
    try:
        chunks = []
        async for chunk in text_to_speech_stream(
            api_key, text, voice, model, response_format
        ):
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception as e:
        print(f"TTS error: {e}")
        return None
