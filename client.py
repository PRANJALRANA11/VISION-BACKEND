import asyncio
import websockets
import pyaudio
import base64
import cv2
from PIL import Image
import io
import json

# --- Configuration ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000 # For sending audio
WEBSOCKET_URI = "ws://localhost:8000/ws" 
CHUNK = 1024

async def send_audio_and_video(uri):
    audio = pyaudio.PyAudio()
    
    # Stream for sending audio to server
    stream_send = audio.open(format=FORMAT,
                             channels=CHANNELS,
                             rate=RATE,
                             input=True,
                             frames_per_buffer=CHUNK)

    # Stream for playing audio received from server
    stream_play = None # Initialize as None, will be opened when first audio from Gemini arrives

    cap = cv2.VideoCapture(0)

    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri, max_size=None, ping_interval=None) as ws:
            print("Connected successfully! Starting streams...")

            async def send_audio():
                while True:
                    data = await asyncio.to_thread(stream_send.read, CHUNK, exception_on_overflow=False)
                    encoded = base64.b64encode(data).decode('utf-8')
                    await ws.send(json.dumps({
                        "type": "audio",
                        "data": encoded
                    }))

            async def send_video():
                while True:
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        await asyncio.sleep(0.01) # Small sleep to prevent busy-waiting
                        continue

                    def encode_frame(frame_to_encode):
                        rgb = cv2.cvtColor(frame_to_encode, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(rgb)
                        img.thumbnail((640, 480))
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG")
                        return base64.b64encode(buf.getvalue()).decode('utf-8')

                    encoded = await asyncio.to_thread(encode_frame, frame)
                    
                    await ws.send(json.dumps({
                        "type": "frame",
                        "data": encoded
                    }))
                    
                    await asyncio.sleep(0.1) # 10 FPS

            async def receive_messages():
                nonlocal stream_play # Allow modification of stream_play from outer scope
                while True:
                    try:
                        message = await ws.recv()
                        msg_json = json.loads(message)
                        msg_type = msg_json.get("type")

                        if msg_type == "audio_from_gemini":
                            audio_data_b64 = msg_json["data"]
                            audio_data_bytes = base64.b64decode(audio_data_b64)
                            sample_rate = msg_json.get("sample_rate", 24000) # Default if not provided
                            audio_format = pyaudio.paInt16 # Assuming int16 as sent by server

                            if stream_play is None:
                                stream_play = audio.open(format=audio_format,
                                                         channels=CHANNELS,
                                                         rate=sample_rate,
                                                         output=True)
                            
                            await asyncio.to_thread(stream_play.write, audio_data_bytes)
                            # print("Playing audio from Gemini...") # For debugging client side

                        elif msg_type == "ai":
                            print(f"AI: {msg_json['data']}")
                        elif msg_type == "user":
                            print(f"You: {msg_json['data']}")
                        elif msg_type == "error":
                            print(f"Server Error: {msg_json['data']}")
                        # Add more message types as needed

                    except websockets.exceptions.ConnectionClosedOK:
                        print("WebSocket connection closed normally.")
                        break
                    except websockets.exceptions.ConnectionClosedError as e:
                        print(f"WebSocket connection closed with error: {e}")
                        break
                    except Exception as e:
                        print(f"Error receiving message: {e}")
                        break


            # Run all tasks concurrently
            await asyncio.gather(send_audio(), send_video(), receive_messages())

    except websockets.exceptions.ConnectionClosed as e:
        print(f"Connection closed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print("Cleaning up resources...")
        if stream_send:
            stream_send.stop_stream()
            stream_send.close()
        if stream_play:
            stream_play.stop_stream()
            stream_play.close()
        audio.terminate()
        cap.release()

if __name__ == "__main__":
    try:
        asyncio.run(send_audio_and_video(WEBSOCKET_URI))
    except KeyboardInterrupt:
        print("Program interrupted by user.")