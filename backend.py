from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Get_started_LiveAPI import AudioLoop  
import asyncio

app = FastAPI()

# @app.websocket("/ws/stream")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()

#     stream = AudioLoop(video_mode="camera", websocket=websocket)

#     try:
#         await stream.run_with_websocket()  # defined below
#     except WebSocketDisconnect:
#         print("WebSocket disconnected")


@app.websocket("/ws")
async def stream_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = AudioLoop(video_mode="camera", websocket=websocket)
    await loop.run_with_websocket()