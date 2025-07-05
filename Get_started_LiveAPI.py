import asyncio
import base64
import io
import cv2
import pyaudio
import PIL.Image
from google import genai
import re
import json # Import json for sending JSON messages

# Audio constants
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

# Gemini + PyAudio setup
MODEL = "models/gemini-2.0-flash-live-001"
CONFIG = {"response_modalities": ["AUDIO" ],  "output_audio_transcription": {} ,"input_audio_transcription": {}  , "speech_config": {
        "language_code": "en-IN"
    }}
pya = pyaudio.PyAudio()

class AudioLoop:
    def __init__(self, video_mode="camera", websocket=None):
        self.video_mode = video_mode
        self.websocket = websocket
        self.audio_in_queue = None
        self.out_queue = None
        self.session = None
        # self.is_playing_audio = asyncio.Event() # Not needed if not playing audio locally

    # Removed _get_frame and get_frames as per client code's responsibility
    # async def _get_frame(self, cap):
    #     ret, frame = cap.read()
    #     if not ret:
    #         return None
    #     frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    #     img = PIL.Image.fromarray(frame_rgb)
    #     img.thumbnail([640, 480])

    #     image_io = io.BytesIO()
    #     img.save(image_io, format="jpeg")
    #     image_io.seek(0)

    #     base64_data = base64.b64encode(image_io.read()).decode()
    #     return base64_data

    # async def get_frames(self):
    #     cap = cv2.VideoCapture(0)
    #     while True:
    #         frame = await asyncio.to_thread(self._get_frame, cap)
    #         if frame:
    #             await self.websocket.send_json({
    #                 "type": "frame",
    #                 "data": frame
    #             })
    #         await asyncio.sleep(0.1)  # 10 fps
    #     cap.release()

    # Removed send_text_ws as it's not relevant to the audio/video stream
    # async def send_text_ws(self):
    #     while True:
    #         text = await asyncio.to_thread(
    #             input,
    #             "message > ",
    #         )
    #         print(f"Sending text: {text}")
    #         if text.lower() == "q":
    #             break
    #         await self.session.send(input=text or ".", end_of_turn=True)
            
    async def send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send(input=msg)

    # The listen_audio function is for capturing audio from a local microphone.
    # Since the client is sending audio, this function can be removed or commented out.
    # async def listen_audio(self):
    #     mic_info = pya.get_default_input_device_info()
    #     self.audio_stream = await asyncio.to_thread(
    #         pya.open,
    #         format=FORMAT,
    #         channels=CHANNELS,
    #         rate=SEND_SAMPLE_RATE,
    #         input=True,
    #         input_device_index=mic_info["index"],
    #         frames_per_buffer=CHUNK_SIZE,
    #     )

    #     while True:
    #         if self.is_playing_audio.is_set():
    #             await asyncio.sleep(0.05)
    #             continue

    #         try:
    #             data = await asyncio.to_thread(
    #                 self.audio_stream.read,
    #                 CHUNK_SIZE,
    #                 exception_on_overflow=False  # <-- important
    #             )
    #             # print("audio" , data   )
    #             await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
    #         except OSError as e:
    #             print("Audio read failed:", e)
    #             await asyncio.sleep(0.05)


    async def receive_audio(self):
        ai_buffer = ""
        while True:
            turn = self.session.receive()
            async for response in turn:
                if response.server_content and response.server_content.output_transcription:
                    
                    ai_text = response.server_content.output_transcription.text
                    ai_buffer += ai_text
                    # print(ai_buffer) # For debugging server side
                    matches = re.findall(r".*?[.?!](?:\s|$)", ai_buffer)
                    for match in matches:
                        await self.websocket.send_json({
                            "type": "ai",
                            "data": match.strip()
                        })
                    ai_buffer = ai_buffer[len("".join(matches)):]   
                        
                if response.server_content.input_transcription:
                    await self.websocket.send_json({
                        "type": "user",
                        "data": response.server_content.input_transcription.text
                    })
                elif response.data:
                    # Instead of putting in audio_in_queue for local playback,
                    # send it over the websocket to the client
                    audio_bytes = response.data
                    encoded_audio = base64.b64encode(audio_bytes).decode('utf-8')
                    await self.websocket.send_json({
                        "type": "audio_from_gemini", # A distinct type for Gemini's audio
                        "data": encoded_audio,
                        "sample_rate": RECEIVE_SAMPLE_RATE, # Inform the client about sample rate
                        "format": "int16" # Inform the client about format
                    })

            # Drain old audio - this might not be strictly necessary here
            # since we're not buffering for local playback anymore.
            while not self.audio_in_queue.empty():
                self.audio_in_queue.get_nowait()

    # Removed play_audio as audio will be played on the client side
    # async def play_audio(self):
    #     stream = await asyncio.to_thread(
    #         pya.open,
    #         format=FORMAT,
    #         channels=CHANNELS,
    #         rate=RECEIVE_SAMPLE_RATE,
    #         output=True,
    #     )
    #     while True:
    #         bytestream = await self.audio_in_queue.get()
    #         self.is_playing_audio.set()
    #         await asyncio.to_thread(stream.write, bytestream)
    #         await asyncio.sleep(0.05)
    #         self.is_playing_audio.clear()


    async def handle_client_stream(self):
        """
        Handles incoming audio/video frames from the client device via WebSocket.
        Sends audio to Gemini, and optionally forwards video frames to frontend.
        """
        while True:
            try:
                message = await self.websocket.receive_json()
                msg_type = message.get("type")
               
                if msg_type == "frame":
                    # Optional: forward frame to frontend, store, or process
                    # self.websocket.send_json({
                    #     "type": "frame_echo", # Echoing back to the client if needed
                    #     "data": message["data"]
                    # })

                    # Send frame to Gemini
                    image_bytes = base64.b64decode(message["data"])
                    await self.out_queue.put({
                        "data": image_bytes,
                        "mime_type": "image/jpeg"
                    })

                elif msg_type == "audio":
                   
                    audio_data = base64.b64decode(message["data"])
                    await self.out_queue.put({
                        "data": audio_data,
                        "mime_type": "audio/pcm"
                    })

            except Exception as e:
                print(f"[Client Stream Error] {e}")
                break


    async def run_with_websocket(self):
        try:
            client = genai.Client(http_options={"api_version": "v1beta"})
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                self.audio_in_queue = asyncio.Queue() # Keep the queue for now, though it's not for local playback
                self.out_queue = asyncio.Queue(maxsize=5)

                
                tg.create_task(self.send_realtime())
                # tg.create_task(self.listen_audio()) # Removed
                tg.create_task(self.receive_audio())
                # tg.create_task(self.play_audio()) # Removed
                tg.create_task(self.handle_client_stream())

        except* Exception as eg:  # Python 3.11+ feature
            errors = "\n".join([f"{type(e).__name__}: {e}" for e in eg.exceptions])
            await self.websocket.send_json({"type": "error", "data": f"TaskGroup errors:\n{errors}"})