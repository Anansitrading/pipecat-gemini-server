#!/usr/bin/env python3
"""FastAPI server wrapper for Pipecat Gemini bot.

This server provides HTTP endpoints for creating Daily rooms and starting bot sessions.
Designed for Railway.app deployment with proper host/port configuration.
"""

import argparse
import os
import subprocess
from contextlib import asynccontextmanager
from typing import Any, Dict

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pipecat.transports.daily.helpers.daily_rest import DailyRESTHelper, DailyRoomParams

load_dotenv(override=True)

# Store bot processes
bot_procs: Dict[int, tuple] = {}

# Daily API helper
daily_helpers: Dict[str, DailyRESTHelper] = {}


def cleanup():
    """Terminate all running bot processes.
    
    Called during server shutdown.
    """
    for entry in bot_procs.values():
        proc = entry[0]
        proc.terminate()
        proc.wait()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager that handles startup and shutdown tasks.
    
    - Creates aiohttp session
    - Initializes Daily API helper
    - Cleans up resources on shutdown
    """
    aiohttp_session = aiohttp.ClientSession()
    daily_helpers["rest"] = DailyRESTHelper(
        daily_api_key=os.getenv("DAILY_API_KEY", ""),
        daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
        aiohttp_session=aiohttp_session,
    )
    yield
    await aiohttp_session.close()
    cleanup()


# Initialize FastAPI app with lifespan manager
app = FastAPI(lifespan=lifespan, title="Pipecat Gemini Bot")

# Configure CORS to allow requests from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health_check():
    """Health check endpoint for Railway.
    
    Returns:
        JSONResponse: Status and environment info
    """
    return JSONResponse({
        "status": "ok",
        "service": "pipecat-gemini-bot",
        "version": "1.0.0",
        "daily_api_configured": bool(os.getenv("DAILY_API_KEY")),
        "google_api_configured": bool(os.getenv("GOOGLE_API_KEY")),
    })


@app.get("/health")
async def health():
    """Alternative health endpoint.
    
    Returns:
        JSONResponse: Health status
    """
    return JSONResponse({"status": "healthy"})


async def create_room_and_token() -> tuple[str, str]:
    """Helper function to create a Daily room and generate an access token.
    
    Returns:
        tuple[str, str]: A tuple containing (room_url, token)
        
    Raises:
        HTTPException: If room creation or token generation fails
    """
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")
    
    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")
    
    return room.url, token


@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
    """Connect endpoint that creates a room and returns connection credentials.
    
    This endpoint is called by client to establish a connection.
    
    Returns:
        Dict[Any, Any]: Authentication bundle containing room_url and token
        
    Raises:
        HTTPException: If room creation, token generation, or bot startup fails
    """
    print("Creating room for RTVI connection")
    room_url, token = await create_room_and_token()
    print(f"Room URL: {room_url}")
    
    # Start the bot process
    try:
        bot_file = "bot-gemini"
        proc = subprocess.Popen(
            [f"python3 -m {bot_file} -u {room_url} -t {token}"],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")
    
    # Return the authentication bundle in format expected by DailyTransport
    return {"room_url": room_url, "token": token}


if __name__ == "__main__":
    import uvicorn
    
    # Parse command line arguments for server configuration
    # Railway provides PORT environment variable - use 0.0.0.0 for host
    default_host = os.getenv("HOST", "0.0.0.0")
    default_port = int(os.getenv("PORT", os.getenv("FAST_API_PORT", "8000")))
    
    parser = argparse.ArgumentParser(description="Pipecat Gemini Bot FastAPI server")
    parser.add_argument("--host", type=str, default=default_host, help="Host address")
    parser.add_argument("--port", type=int, default=default_port, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Reload code on change")
    
    config = parser.parse_args()
    
    print(f"🚀 Starting Pipecat Gemini Bot server on {config.host}:{config.port}")
    print(f"   DAILY_API_KEY configured: {bool(os.getenv('DAILY_API_KEY'))}")
    print(f"   GOOGLE_API_KEY configured: {bool(os.getenv('GOOGLE_API_KEY'))}")
    
    # Start the FastAPI server
    uvicorn.run(
        "server:app",
        host=config.host,
        port=config.port,
        reload=config.reload,
    )
