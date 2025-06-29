import asyncio
import base64
import io
import cv2
import pyaudio
import PIL.Image
from google import genai
import re
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
        self.is_playing_audio = asyncio.Event()

    def _get_frame(self, cap):
        ret, frame = cap.read()
        if not ret:
            return None
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(frame_rgb)
        img.thumbnail([640, 480])

        image_io = io.BytesIO()
        img.save(image_io, format="jpeg")
        image_io.seek(0)

        base64_data = base64.b64encode(image_io.read()).decode()
        return base64_data

    async def get_frames(self):
        cap = cv2.VideoCapture(0)
        while True:
            frame = await asyncio.to_thread(self._get_frame, cap)
            if frame:
                await self.websocket.send_json({
                    "type": "frame",
                    "data": frame
                })
            await asyncio.sleep(0.1)  # 10 fps
        cap.release()

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

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )

        while True:
            if self.is_playing_audio.is_set():
                await asyncio.sleep(0.05)
                continue

            try:
                data = await asyncio.to_thread(
                    self.audio_stream.read,
                    CHUNK_SIZE,
                    exception_on_overflow=False  # <-- important
                )
                await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
            except OSError as e:
                print("Audio read failed:", e)
                await asyncio.sleep(0.05)


    async def receive_audio(self):
        ai_buffer = ""
        while True:
            turn = self.session.receive()
            async for response in turn:
                if response.server_content and response.server_content.output_transcription:
                    
                    ai_text = response.server_content.output_transcription.text
                    ai_buffer += ai_text
                    print(ai_buffer)
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
                    self.audio_in_queue.put_nowait(response.data)

            # Drain old audio
            while not self.audio_in_queue.empty():
                self.audio_in_queue.get_nowait()

    async def play_audio(self):
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )
        while True:
            bytestream = await self.audio_in_queue.get()
            self.is_playing_audio.set()
            await asyncio.to_thread(stream.write, bytestream)
            await asyncio.sleep(0.05)
            self.is_playing_audio.clear()

    async def run_with_websocket(self):
        try:
            client = genai.Client(http_options={"api_version": "v1beta"})
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                
                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())
                tg.create_task(self.get_frames())

        except* Exception as eg:  # Python 3.11+ feature
            errors = "\n".join([f"{type(e).__name__}: {e}" for e in eg.exceptions])
            await self.websocket.send_json({"type": "error", "data": f"TaskGroup errors:\n{errors}"})
