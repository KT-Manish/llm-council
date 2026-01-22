"""FastAPI backend for KT LLM Council."""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
from typing import List, Dict, Any, Optional
import uuid
import json
import asyncio

from . import storage
from . import users
from .auth import hash_password, verify_password, create_token
from .middleware import get_current_user, get_current_admin
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings
from .voice import VoiceChatSession
from .config import OPENAI_API_KEY, TTS_VOICE, ADMIN_EMAIL, ADMIN_PASSWORD

app = FastAPI(title="KT LLM Council API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:3000", "https://llm-debate.kuware.ai"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Request/Response Models
# ============================================================

class LoginRequest(BaseModel):
    """Login request with email and password."""
    email: str
    password: str


class LoginResponse(BaseModel):
    """Login response with token and user info."""
    token: str
    user: Dict[str, Any]


class CreateUserRequest(BaseModel):
    """Request to create a new user."""
    email: str
    password: str
    name: str
    is_admin: bool = False


class UserResponse(BaseModel):
    """User response without password."""
    id: str
    email: str
    name: str
    is_admin: bool
    created_at: str


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


# ============================================================
# Startup Event - Create Admin User
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Create default admin user if no users exist."""
    if not users.user_exists():
        password_hash = hash_password(ADMIN_PASSWORD)
        users.create_user(
            email=ADMIN_EMAIL,
            password_hash=password_hash,
            name="Admin",
            is_admin=True
        )
        print(f"Created default admin user: {ADMIN_EMAIL}")


# ============================================================
# Health Check
# ============================================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "KT LLM Council API"}


# ============================================================
# Authentication Endpoints
# ============================================================

@app.post("/api/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login with email and password."""
    user = users.get_user_by_email(request.email)

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user["id"], user["email"], user["is_admin"])

    # Remove password_hash from response
    user_response = {k: v for k, v in user.items() if k != "password_hash"}

    return {"token": token, "user": user_response}


@app.get("/api/auth/me", response_model=UserResponse)
async def get_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get current user info."""
    return current_user


# ============================================================
# Admin Endpoints
# ============================================================

@app.get("/api/admin/users", response_model=List[UserResponse])
async def list_users(current_user: Dict[str, Any] = Depends(get_current_admin)):
    """List all users (admin only)."""
    return users.list_users()


@app.post("/api/admin/users", response_model=UserResponse)
async def create_user(
    request: CreateUserRequest,
    current_user: Dict[str, Any] = Depends(get_current_admin)
):
    """Create a new user (admin only)."""
    try:
        password_hash = hash_password(request.password)
        new_user = users.create_user(
            email=request.email,
            password_hash=password_hash,
            name=request.name,
            is_admin=request.is_admin
        )
        return new_user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: Dict[str, Any] = Depends(get_current_admin)
):
    """Delete a user (admin only)."""
    # Prevent admin from deleting themselves
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    deleted = users.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "User deleted successfully"}


# ============================================================
# Conversation Endpoints (Protected)
# ============================================================

@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations(current_user: Dict[str, Any] = Depends(get_current_user)):
    """List all conversations for the current user (metadata only)."""
    return storage.list_conversations(user_id=current_user["id"])


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(
    request: CreateConversationRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id, user_id=current_user["id"])
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check ownership
    if conversation.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check ownership
    if conversation.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result
    )

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(
    conversation_id: str,
    request: SendMessageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check ownership
    if conversation.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(request.content, stage1_results)
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(request.content, stage1_results, stage2_results)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.websocket("/api/conversations/{conversation_id}/voice")
async def voice_chat_endpoint(
    websocket: WebSocket,
    conversation_id: str,
    token: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for voice chat.
    Handles audio streaming, transcription, and TTS responses.
    """
    # Verify token from query parameter
    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return

    from .auth import verify_token
    from .users import get_user_by_id

    payload = verify_token(token)
    if payload is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    current_user = get_user_by_id(payload.get("sub"))
    if current_user is None:
        await websocket.close(code=4001, reason="User not found")
        return

    # Check if OpenAI API key is configured
    if not OPENAI_API_KEY:
        await websocket.close(code=4001, reason="OpenAI API key not configured")
        return

    # Check if conversation exists and user owns it
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        await websocket.close(code=4004, reason="Conversation not found")
        return

    if conversation.get("user_id") != current_user["id"]:
        await websocket.close(code=4003, reason="Access denied")
        return

    await websocket.accept()

    # Create voice chat session
    session = VoiceChatSession(
        websocket=websocket,
        conversation_id=conversation_id,
        api_key=OPENAI_API_KEY,
        tts_voice=TTS_VOICE
    )

    try:
        await session.run()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
