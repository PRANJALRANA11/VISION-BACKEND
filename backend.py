from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Get_started_LiveAPI import create_audio_loop,logger
    
import asyncio
import uuid

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
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = str(uuid.uuid4())
    
    try:
        await create_audio_loop(client_id, websocket)
    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected")
    except Exception as e:
        logger.error(f"Error with client {client_id}: {e}")