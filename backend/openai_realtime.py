"""OpenAI Realtime API WebSocket client for speech-to-text."""

import asyncio
import base64
import json
import websockets
from typing import Optional, Callable, Awaitable


class RealtimeClient:
    """WebSocket client for OpenAI Realtime API."""

    REALTIME_API_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.transcription: str = ""
        self._on_transcription: Optional[Callable[[str], Awaitable[None]]] = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to OpenAI Realtime API."""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "realtime=v1"
            }
            self.ws = await websockets.connect(
                self.REALTIME_API_URL,
                additional_headers=headers
            )
            self._connected = True

            # Configure session for audio transcription only
            await self._configure_session()

            return True
        except Exception as e:
            print(f"Failed to connect to OpenAI Realtime API: {e}")
            return False

    async def _configure_session(self):
        """Configure the realtime session for transcription."""
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": None  # Manual turn detection
            }
        }
        await self.ws.send(json.dumps(session_config))

    async def send_audio(self, audio_data: bytes):
        """Send audio data to the API.

        Args:
            audio_data: Raw PCM16 audio bytes
        """
        if not self._connected or not self.ws:
            return

        # Encode audio as base64 and send
        audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        message = {
            "type": "input_audio_buffer.append",
            "audio": audio_base64
        }
        await self.ws.send(json.dumps(message))

    async def commit_audio(self):
        """Commit the audio buffer and request transcription."""
        if not self._connected or not self.ws:
            return

        # Commit the audio buffer
        await self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        # Create a response to trigger transcription
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "modalities": ["text"]
            }
        }))

    async def receive_messages(self) -> Optional[str]:
        """Receive messages and extract transcription.

        Returns:
            The final transcription text, or None if not received
        """
        if not self._connected or not self.ws:
            return None

        transcription = ""

        try:
            async for message in self.ws:
                data = json.loads(message)
                event_type = data.get("type", "")

                # Handle transcription events
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcription = data.get("transcript", "")
                    if self._on_transcription:
                        await self._on_transcription(transcription)
                    return transcription

                # Handle response text (fallback)
                elif event_type == "response.text.done":
                    text = data.get("text", "")
                    if text and not transcription:
                        transcription = text

                # Handle response completion
                elif event_type == "response.done":
                    if transcription:
                        return transcription

                # Handle errors
                elif event_type == "error":
                    error = data.get("error", {})
                    print(f"Realtime API error: {error}")
                    return None

        except websockets.exceptions.ConnectionClosed:
            self._connected = False
            return transcription if transcription else None
        except Exception as e:
            print(f"Error receiving messages: {e}")
            return transcription if transcription else None

        return transcription if transcription else None

    async def close(self):
        """Close the WebSocket connection."""
        self._connected = False
        if self.ws:
            await self.ws.close()
            self.ws = None

    def set_transcription_callback(self, callback: Callable[[str], Awaitable[None]]):
        """Set callback for when transcription is received."""
        self._on_transcription = callback


async def transcribe_audio(api_key: str, audio_chunks: list[bytes]) -> Optional[str]:
    """Transcribe audio using OpenAI Realtime API.

    Args:
        api_key: OpenAI API key
        audio_chunks: List of PCM16 audio chunks

    Returns:
        Transcribed text or None on failure
    """
    client = RealtimeClient(api_key)

    try:
        if not await client.connect():
            return None

        # Send all audio chunks
        for chunk in audio_chunks:
            await client.send_audio(chunk)

        # Commit and get transcription
        await client.commit_audio()
        transcription = await client.receive_messages()

        return transcription

    finally:
        await client.close()
