"""Voice chat session management."""

import asyncio
import base64
import json
from typing import Optional, Callable, Awaitable
from fastapi import WebSocket

from .openai_realtime import RealtimeClient
from .tts import text_to_speech_stream
from .council import (
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
    generate_conversation_title
)
from . import storage


class VoiceChatSession:
    """Manages a voice chat session with transcription and TTS."""

    def __init__(
        self,
        websocket: WebSocket,
        conversation_id: str,
        api_key: str,
        tts_voice: str = "alloy"
    ):
        self.websocket = websocket
        self.conversation_id = conversation_id
        self.api_key = api_key
        self.tts_voice = tts_voice
        self.audio_chunks: list[bytes] = []
        self.realtime_client: Optional[RealtimeClient] = None
        self._is_recording = False

    async def send_event(self, event_type: str, data: dict = None):
        """Send an event to the client."""
        message = {"type": event_type}
        if data:
            message.update(data)
        await self.websocket.send_json(message)

    async def handle_message(self, message: dict) -> bool:
        """Handle a message from the client.

        Returns:
            False if session should end, True otherwise
        """
        msg_type = message.get("type")

        if msg_type == "start_recording":
            await self._start_recording()

        elif msg_type == "audio":
            # Decode base64 audio and accumulate
            audio_data = message.get("data", "")
            if audio_data:
                decoded = base64.b64decode(audio_data)
                self.audio_chunks.append(decoded)
                # Stream to realtime API if connected
                if self.realtime_client:
                    await self.realtime_client.send_audio(decoded)

        elif msg_type == "stop_recording":
            await self._stop_recording()

        elif msg_type == "close":
            return False

        return True

    async def _start_recording(self):
        """Initialize recording and connect to realtime API."""
        print("[Voice] Starting recording...")
        self.audio_chunks = []
        self._is_recording = True

        # Connect to OpenAI Realtime API
        print("[Voice] Connecting to OpenAI Realtime API...")
        self.realtime_client = RealtimeClient(self.api_key)
        connected = await self.realtime_client.connect()

        if not connected:
            print("[Voice] Failed to connect to OpenAI Realtime API")
            await self.send_event("error", {"message": "Failed to connect to transcription service"})
            return

        print("[Voice] Connected to OpenAI Realtime API")
        await self.send_event("recording_started")

    async def _stop_recording(self):
        """Stop recording and process the audio."""
        print("[Voice] Stop recording received")
        self._is_recording = False

        if not self.realtime_client:
            print("[Voice] No realtime client active")
            await self.send_event("error", {"message": "No recording session active"})
            return

        try:
            # Commit audio and get transcription
            print("[Voice] Committing audio to OpenAI...")
            await self.realtime_client.commit_audio()
            print("[Voice] Waiting for transcription...")
            transcription = await self.realtime_client.receive_messages()
            try:
                print(f"[Voice] Transcription received: {transcription}")
            except UnicodeEncodeError:
                print(f"[Voice] Transcription received (length: {len(transcription) if transcription else 0})")

            if not transcription or not transcription.strip():
                print("[Voice] No speech detected")
                await self.send_event("error", {"message": "No speech detected"})
                return

            # Send transcription to client
            await self.send_event("transcription", {"text": transcription})
            print("[Voice] Starting council process...")

            # Run council process with transcribed text
            await self._run_council_process(transcription)

        except Exception as e:
            print(f"[Voice] Error in stop_recording: {type(e).__name__}: {e}")
            await self.send_event("error", {"message": str(e)})
        finally:
            print("[Voice] Closing realtime client")
            await self.realtime_client.close()
            self.realtime_client = None

    async def _run_council_process(self, user_query: str):
        """Run the 3-stage council process and stream audio response."""
        # Check conversation exists
        conversation = storage.get_conversation(self.conversation_id)
        if not conversation:
            await self.send_event("error", {"message": "Conversation not found"})
            return

        is_first_message = len(conversation["messages"]) == 0

        # Add user message
        storage.add_user_message(self.conversation_id, user_query)

        # Start title generation in parallel if first message
        title_task = None
        if is_first_message:
            title_task = asyncio.create_task(generate_conversation_title(user_query))

        # Stage 1
        await self.send_event("stage1_start")
        stage1_results = await stage1_collect_responses(user_query)
        await self.send_event("stage1_complete", {"data": stage1_results})

        # Stage 2
        await self.send_event("stage2_start")
        stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results)
        aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
        await self.send_event("stage2_complete", {
            "data": stage2_results,
            "metadata": {
                "label_to_model": label_to_model,
                "aggregate_rankings": aggregate_rankings
            }
        })

        # Stage 3
        await self.send_event("stage3_start")
        stage3_result = await stage3_synthesize_final(user_query, stage1_results, stage2_results)
        await self.send_event("stage3_complete", {"data": stage3_result})

        # Save assistant message
        storage.add_assistant_message(
            self.conversation_id,
            stage1_results,
            stage2_results,
            stage3_result
        )

        # Handle title
        if title_task:
            title = await title_task
            storage.update_conversation_title(self.conversation_id, title)
            await self.send_event("title_complete", {"data": {"title": title}})

        # Convert stage 3 response to speech
        await self._stream_audio_response(stage3_result.get("response", ""))

    async def _stream_audio_response(self, text: str):
        """Stream TTS audio response to client."""
        if not text:
            await self.send_event("audio_complete")
            return

        try:
            await self.send_event("audio_start")

            async for chunk in text_to_speech_stream(
                self.api_key,
                text,
                voice=self.tts_voice,
                model="tts-1",
                response_format="mp3"
            ):
                # Send audio chunk as base64
                audio_base64 = base64.b64encode(chunk).decode("utf-8")
                await self.send_event("audio_response", {"data": audio_base64})

            await self.send_event("audio_complete")

        except Exception as e:
            await self.send_event("error", {"message": f"TTS error: {str(e)}"})

    async def run(self):
        """Main session loop."""
        try:
            while True:
                try:
                    data = await self.websocket.receive_json()
                    print(f"[Voice] Received message: {data.get('type', 'unknown')}")
                    should_continue = await self.handle_message(data)
                    if not should_continue:
                        break
                except Exception as e:
                    print(f"[Voice] Error in message loop: {type(e).__name__}: {e}")
                    raise
        except Exception as e:
            print(f"[Voice] Session error: {type(e).__name__}: {e}")
            try:
                await self.send_event("error", {"message": str(e)})
            except:
                pass
        finally:
            print("[Voice] Session ending, cleaning up")
            if self.realtime_client:
                await self.realtime_client.close()
