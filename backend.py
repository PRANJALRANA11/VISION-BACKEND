from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Get_started_LiveAPI import AudioLoop  
import asyncio

app = FastAPI()

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    stream = AudioLoop(video_mode="camera", websocket=websocket)

    try:
        await stream.run_with_websocket()  # defined below
    except WebSocketDisconnect:
        print("WebSocket disconnected")
