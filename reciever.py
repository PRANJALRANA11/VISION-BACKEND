#!/usr/bin/env python3
"""
Receiver Client Example
This script connects to the server as a receiver,
displays video frames from the broadcaster,
and shows AI responses.
"""

import asyncio
import websockets
import json
import base64
import cv2
import pyaudio
import numpy as np
import threading
import queue
from typing import Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Audio playback configuration
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 24000  # Gemini audio output rate
CHUNK = 1024

class ReceiverClient:
    def __init__(self, server_url: str = "ws://localhost:8000"): # Removed /ws from here
        self.server_url = server_url
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.running = False # Flag to control main loop and threads
        
        # Audio setup for playback
        self.audio = pyaudio.PyAudio()
        self.audio_stream = None
        self.audio_queue = queue.Queue() # Thread-safe queue for audio data
        self.audio_playback_task = None # To hold the asyncio task for audio
        
        # Video display
        self.current_frame = None
        self.frame_update_event = asyncio.Event() # Event to signal new frame
        
        # Threading/Task management
        self.listener_task = None
        self.status_task = None
        self.display_task = None # Will be used if video display is an asyncio task
        
    async def connect(self):
        """Connect to the WebSocket server and set role."""
        try:
            self.websocket = await websockets.connect(self.server_url)
            logger.info(f"Connected to server at {self.server_url}")
            
            # Set role as receiver
            await self.websocket.send(json.dumps({
                "type": "set_role",
                "role": "receiver"
            }))
            
            # Wait for role confirmation to ensure connection is established and role is set
            response = await self.websocket.recv()
            data = json.loads(response)
            if data.get("type") == "role_confirmed" and data.get("role") == "receiver":
                logger.info(f"Role confirmed: {data.get('role')}")
                return True
            else:
                logger.error(f"Role confirmation failed: {data.get('message', 'Unknown error')}")
                await self.websocket.close()
                self.websocket = None
                return False
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from the server and clean up resources."""
        if not self.running:
            return # Already stopping or stopped

        self.running = False
        logger.info("Initiating disconnection...")

        # Cancel asyncio tasks
        if self.listener_task and not self.listener_task.done():
            self.listener_task.cancel()
        if self.status_task and not self.status_task.done():
            self.status_task.cancel()
        if self.display_task and not self.display_task.done():
            self.display_task.cancel()
        if self.audio_playback_task and not self.audio_playback_task.done():
             self.audio_playback_task.cancel()

        # Allow tasks to finish cancellation
        await asyncio.sleep(0.1)

        # Stop audio stream
        if self.audio_stream:
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            logger.info("Audio stream closed.")
        
        # Terminate PyAudio
        if self.audio:
            self.audio.terminate()
            logger.info("PyAudio terminated.")
        
        # Close OpenCV windows
        cv2.destroyAllWindows()
        logger.info("OpenCV windows destroyed.")
        
        # Close websocket
        if self.websocket:
            try:
                await self.websocket.send(json.dumps({"type": "disconnect"}))
                await self.websocket.close()
                logger.info("WebSocket closed.")
            except Exception as e:
                logger.warning(f"Error closing websocket: {e}")
            self.websocket = None
        
        logger.info("Disconnected from server.")
    
    def setup_audio_playback_stream(self):
        """Setup audio output stream for playing Gemini responses."""
        try:
            self.audio_stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                output=True,
                frames_per_buffer=CHUNK
            )
            logger.info("Audio playback stream setup complete.")
            return True
        except Exception as e:
            logger.error(f"Failed to setup audio playback stream: {e}")
            return False
    
    async def audio_playback_worker(self):
        """Asynchronous worker to play audio from the queue."""
        if not self.setup_audio_playback_stream():
            logger.error("Audio playback worker cannot start without stream setup.")
            return

        while self.running:
            try:
                # Get audio data from queue with a timeout
                audio_data = await asyncio.to_thread(self.audio_queue.get, timeout=0.1)
                if self.audio_stream and self.audio_stream.is_active():
                    await asyncio.to_thread(self.audio_stream.write, audio_data)
            except queue.Empty:
                await asyncio.sleep(0.01) # Small sleep if queue is empty
                continue
            except asyncio.CancelledError:
                logger.info("Audio playback worker cancelled.")
                break
            except Exception as e:
                logger.error(f"Audio playback error: {e}")
                await asyncio.sleep(0.1) # Prevent tight loop on error


    async def video_display_worker(self):
        """Asynchronous worker to display video frames."""
        cv2.namedWindow('Broadcaster Feed', cv2.WINDOW_NORMAL)
        cv2.setWindowProperty('Broadcaster Feed', cv2.WND_PROP_TOPMOST, 1)

        while self.running:
            try:
                # Wait for a new frame, or timeout to check if still running
                await asyncio.wait_for(self.frame_update_event.wait(), timeout=0.1)
                self.frame_update_event.clear() # Clear event for next frame

                if self.current_frame is not None:
                    # Perform imshow in a thread, as it can be blocking for GUI events
                    # However, waitKey *must* be called from the same thread as imshow.
                    # This means the imshow/waitKey pair should be in the same place.
                    # We will call it directly here.
                    cv2.imshow('Broadcaster Feed', self.current_frame)
                    
                    # cv2.waitKey needs to be non-blocking in an asyncio loop, or run in a thread
                    # For simple cases, running it directly is usually fine if it's called frequently
                    # but for responsiveness, it's better to delegate.
                    # Here, we'll use asyncio.to_thread for waitKey.
                    key = await asyncio.to_thread(cv2.waitKey, 1) & 0xFF
                    
                    if key == 27:  # ESC key
                        logger.info("ESC pressed, stopping...")
                        self.running = False # Signal to stop all tasks
                        break
                    
                    # Check if window was manually closed
                    if cv2.getWindowProperty('Broadcaster Feed', cv2.WND_PROP_VISIBLE) < 1:
                        logger.info("Video window closed, stopping...")
                        self.running = False # Signal to stop all tasks
                        break
                
                await asyncio.sleep(0.01) # Small delay to yield control and reduce busy-waiting
                
            except asyncio.TimeoutError:
                # No new frame, just continue to check running state
                continue
            except asyncio.CancelledError:
                logger.info("Video display worker cancelled.")
                break
            except Exception as e:
                logger.error(f"Video display error: {e}")
                await asyncio.sleep(0.1) # Prevent tight loop on error

        cv2.destroyAllWindows() # Ensure windows are closed on exit


    async def listen_for_messages(self):
        """Listen for messages from the server."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "frame":
                    try:
                        frame_data = base64.b64decode(data.get('data'))
                        # Use cv2.imdecode to reconstruct the image from bytes
                        frame = cv2.imdecode(np.frombuffer(frame_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                        
                        if frame is not None and frame.size > 0: # Ensure frame is valid
                            self.current_frame = frame
                            self.frame_update_event.set() # Signal new frame available
                        else:
                            logger.warning("Received invalid or empty frame.")
                            
                    except Exception as e:
                        logger.error(f"Error decoding frame: {e}")
                    
                elif msg_type == "ai":
                    print(f"🤖 AI: {data.get('data')}")
                    
                elif msg_type == "user":
                    print(f"👤 User: {data.get('data')}")
                    
                elif msg_type == "audio_from_gemini":
                    try:
                        audio_data = base64.b64decode(data.get('data'))
                        self.audio_queue.put_nowait(audio_data) # Use put_nowait as queue is async-handled
                        # logger.info("Received audio from Gemini") # Too verbose, uncomment for debugging
                    except asyncio.QueueFull:
                        logger.warning("Audio queue full, dropping audio frame.")
                    except Exception as e:
                        logger.error(f"Error decoding audio: {e}")
                    
                elif msg_type == "broadcaster_changed":
                    broadcaster_id = data.get('broadcaster_id')
                    print(f"📡 New broadcaster: {broadcaster_id if broadcaster_id else 'None (Broadcaster disconnected)'}")
                    
                elif msg_type == "error":
                    logger.error(f"Server error: {data.get('data')}")
                    
                elif msg_type == "status":
                    status = data
                    print(f"📊 Status:")
                    print(f"   • Client ID: {status.get('client_id')}")
                    print(f"   • Role: {'Receiver' if status.get('is_receiver') else 'Unknown'}")
                    print(f"   • Broadcaster: {status.get('broadcaster_id', 'None')}")
                    print(f"   • Total clients: {status.get('total_clients', 0)}")
                    print(f"   • Receivers: {status.get('num_receivers', 0)}") # Corrected to num_receivers
                    print("-" * 30)
                else:
                    logger.debug(f"Received unknown message type: {msg_type} - {data}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Connection closed by server: {e.code} - {e.reason}")
        except asyncio.CancelledError:
            logger.info("Listener task cancelled.")
        except Exception as e:
            logger.error(f"Error listening for messages: {e}")
        finally:
            self.running = False # Signal overall shutdown

    async def get_status_loop(self):
        """Periodically requests status from server."""
        while self.running:
            try:
                if self.websocket:
                    await self.websocket.send(json.dumps({"type": "get_status"}))
                await asyncio.sleep(5) # Request status every 5 seconds
            except asyncio.CancelledError:
                logger.info("Status loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in get_status_loop: {e}")
                await asyncio.sleep(1) # Short delay on error

    async def run(self):
        """Main run loop for the receiver client."""
        self.running = True

        if not await self.connect():
            logger.error("Failed to establish WebSocket connection.")
            return # Exit if connection fails

        print("📺 Receiver started! Waiting for broadcaster...")
        print("🎥 Video will appear in a new window (ensure it's not minimized/hidden)")
        print("🔊 Audio will play through your speakers")
        print("💬 AI responses will appear below:")
        print("⌨️  Press ESC in video window or Ctrl+C here to stop")
        print("-" * 50)
        
        # Start all long-running tasks concurrently
        try:
            self.audio_playback_task = asyncio.create_task(self.audio_playback_worker())
            self.display_task = asyncio.create_task(self.video_display_worker())
            self.listener_task = asyncio.create_task(self.listen_for_messages())
            self.status_task = asyncio.create_task(self.get_status_loop())

            # Wait for all tasks to complete (or for self.running to become False)
            while self.running:
                await asyncio.sleep(0.1) # Keep the main loop alive
                # Check if any critical task has failed
                if self.listener_task.done() or self.display_task.done() or self.audio_playback_task.done():
                    logger.error("One or more core tasks have stopped unexpectedly.")
                    self.running = False # Trigger shutdown
                    break

        except KeyboardInterrupt:
            print("\n⏹️  Stopping receiver (KeyboardInterrupt)...")
        except Exception as e:
            logger.critical(f"Unhandled error in main run loop: {e}")
        finally:
            await self.disconnect() # Ensure clean shutdown


# --- SimpleTextReceiver remains mostly the same, as it doesn't do video ---
class SimpleTextReceiver:
    def __init__(self, server_url: str = "ws://localhost:8000"):
        self.server_url = server_url
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.running = False # Added running flag
        
    async def connect(self):
        try:
            self.websocket = await websockets.connect(self.server_url)
            logger.info(f"Connected to server at {self.server_url}")
            
            await self.websocket.send(json.dumps({
                "type": "set_role",
                "role": "receiver"
            }))
            
            # Wait for role confirmation
            response = await self.websocket.recv()
            data = json.loads(response)
            if data.get("type") == "role_confirmed" and data.get("role") == "receiver":
                logger.info(f"Role confirmed: {data.get('role')}")
                return True
            else:
                logger.error(f"Role confirmation failed: {data.get('message', 'Unknown error')}")
                await self.websocket.close()
                self.websocket = None
                return False
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def listen(self):
        try:
            async for message in self.websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "ai":
                    print(f"🤖 AI: {data.get('data')}")
                elif msg_type == "user":
                    print(f"👤 User: {data.get('data')}")
                elif msg_type == "role_confirmed":
                    print(f"✅ Role confirmed: {data.get('role')}")
                elif msg_type == "broadcaster_changed":
                    broadcaster_id = data.get('broadcaster_id')
                    print(f"📡 New broadcaster: {broadcaster_id if broadcaster_id else 'None (Broadcaster disconnected)'}")
                elif msg_type == "status":
                    status = data
                    print(f"📊 Status:")
                    print(f"   • Client ID: {status.get('client_id')}")
                    print(f"   • Role: {'Receiver' if status.get('is_receiver') else 'Unknown'}")
                    print(f"   • Broadcaster: {status.get('broadcaster_id', 'None')}")
                    print(f"   • Total clients: {status.get('total_clients', 0)}")
                    print(f"   • Receivers: {status.get('num_receivers', 0)}")
                    print("-" * 30)
                elif msg_type == "error":
                    logger.error(f"Server error: {data.get('data')}")
                else:
                    logger.debug(f"Received unknown message type: {msg_type} - {data}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed")
        except asyncio.CancelledError:
            logger.info("Listener task cancelled for SimpleTextReceiver.")
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
        finally:
            self.running = False

    async def run(self):
        self.running = True
        if await self.connect():
            print("📱 Simple text receiver started!")
            print("💬 AI responses will appear here:")
            print("-" * 40)
            try:
                listener_task = asyncio.create_task(self.listen())
                status_task = asyncio.create_task(self.get_status_loop())
                while self.running:
                    await asyncio.sleep(0.1)
                    if listener_task.done() or status_task.done():
                        logger.error("Core task stopped unexpectedly in SimpleTextReceiver.")
                        self.running = False
            except KeyboardInterrupt:
                print("\n⏹️  Stopping simple receiver...")
            finally:
                if self.websocket:
                    await self.websocket.send(json.dumps({"type": "disconnect"}))
                    await self.websocket.close()
                logger.info("Simple text receiver disconnected.")
        else:
            logger.error("Failed to connect for SimpleTextReceiver.")

    async def get_status_loop(self):
        """Periodically requests status from server for SimpleTextReceiver."""
        while self.running:
            try:
                if self.websocket:
                    await self.websocket.send(json.dumps({"type": "get_status"}))
                await asyncio.sleep(5) # Request status every 5 seconds
            except asyncio.CancelledError:
                logger.info("Status loop cancelled in SimpleTextReceiver.")
                break
            except Exception as e:
                logger.error(f"Error in SimpleTextReceiver status loop: {e}")
                await asyncio.sleep(1)


async def main():
    server_url = "ws://localhost:8000/ws" # Ensure this matches your server's URL in server.py
    
    print("Choose receiver mode:")
    print("1. Full receiver (video + audio + text)")
    print("2. Simple text-only receiver")
    
    try:
        choice = input("Enter choice (1 or 2): ").strip()
        
        if choice == "2":
            client = SimpleTextReceiver(server_url)
        else:
            client = ReceiverClient(server_url)
        
        await client.run()
        
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")

if __name__ == "__main__":
    print("📺 Receiver Client Starting...")
    print("📋 Make sure the WebSocket server is running")
    print("-" * 50)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye from main process!")
    except Exception as e:
        logger.critical(f"Unhandled exception in asyncio.run: {e}")