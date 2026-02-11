#!/usr/bin/env python

# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import json
import logging
import ssl
import threading
import time
from pathlib import Path

import av
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class CameraVideoTrack(MediaStreamTrack):
    """An aiortc video track that reads frames from a shared camera_frames dict.

    Each instance is associated with one camera name (e.g. "left_wrist"). The
    ``recv()`` coroutine paces output to the target FPS and converts numpy
    BGR/RGB arrays to ``av.VideoFrame``.  A black frame is returned when no
    camera data is available yet.
    """

    kind = "video"

    def __init__(self, camera_name: str, camera_frames: dict, width: int, height: int, fps: int):
        super().__init__()
        self._camera_name = camera_name
        self._camera_frames = camera_frames
        self._width = width
        self._height = height
        self._fps = fps
        self._frame_interval = 1.0 / fps
        self._start_time: float | None = None
        self._frame_count = 0

    async def recv(self) -> av.VideoFrame:
        """Return the next video frame, paced to target FPS."""
        if self._start_time is None:
            self._start_time = time.monotonic()

        # Pace to target FPS
        target_time = self._start_time + self._frame_count * self._frame_interval
        now = time.monotonic()
        if target_time > now:
            await asyncio.sleep(target_time - now)

        self._frame_count += 1

        # Read frame from shared dict
        frame_data = self._camera_frames.get(self._camera_name)

        if frame_data is not None:
            # frame_data should be a numpy array (H, W, C) in BGR or RGB
            frame = av.VideoFrame.from_ndarray(frame_data, format="bgr24")
        else:
            # Return black frame when no data available
            black = np.zeros((self._height, self._width, 3), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(black, format="bgr24")

        frame.pts = self._frame_count
        frame.time_base = f"1/{self._fps}"
        return frame


class VRWebRTCServer:
    """Manages a FastAPI + aiortc server for VR teleoperation.

    Serves static WebXR files over HTTPS (required for WebXR secure context),
    handles WebSocket signaling for WebRTC, and manages peer connections with
    camera video tracks and controller input data channels.
    """

    def __init__(
        self,
        host: str,
        port: int,
        cert_path: str,
        key_path: str,
        camera_names: list[str],
        camera_frames: dict,
        shared_state: dict,
        state_lock: threading.Lock,
        video_width: int = 640,
        video_height: int = 480,
        video_fps: int = 30,
    ):
        self._host = host
        self._port = port
        self._cert_path = cert_path
        self._key_path = key_path
        self._camera_names = camera_names
        self._camera_frames = camera_frames
        self._shared_state = shared_state
        self._state_lock = state_lock
        self._video_width = video_width
        self._video_height = video_height
        self._video_fps = video_fps

        self._peer_connections: set[RTCPeerConnection] = set()
        self._relay = MediaRelay()
        self._app = self._create_app()
        self._server = None

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="LeRobot VR Teleop")

        # Serve static WebXR files
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        async def index():
            return RedirectResponse(url="/static/index.html")

        @app.websocket("/ws/signaling")
        async def signaling(ws: WebSocket):
            await ws.accept()
            pc = RTCPeerConnection()
            self._peer_connections.add(pc)

            # Add camera video tracks
            for cam_name in self._camera_names:
                track = CameraVideoTrack(
                    camera_name=cam_name,
                    camera_frames=self._camera_frames,
                    width=self._video_width,
                    height=self._video_height,
                    fps=self._video_fps,
                )
                pc.addTrack(track)

            # Handle data channel for controller input
            @pc.on("datachannel")
            def on_datachannel(channel):
                logger.info(f"Data channel opened: {channel.label}")

                @channel.on("message")
                def on_message(message):
                    try:
                        data = json.loads(message)
                        if data.get("type") == "controller_state":
                            with self._state_lock:
                                self._shared_state.update(data)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Invalid data channel message received")

            @pc.on("connectionstatechange")
            async def on_connectionstatechange():
                logger.info(f"WebRTC connection state: {pc.connectionState}")
                if pc.connectionState in ("failed", "closed"):
                    await pc.close()
                    self._peer_connections.discard(pc)

            try:
                while True:
                    raw = await ws.receive_text()
                    msg = json.loads(raw)

                    if msg["type"] == "offer":
                        offer = RTCSessionDescription(sdp=msg["sdp"], type=msg["type"])
                        await pc.setRemoteDescription(offer)
                        answer = await pc.createAnswer()
                        await pc.setLocalDescription(answer)
                        await ws.send_json({
                            "type": pc.localDescription.type,
                            "sdp": pc.localDescription.sdp,
                        })

                    elif msg["type"] == "candidate":
                        from aiortc import RTCIceCandidate

                        # Parse ICE candidate
                        candidate_str = msg.get("candidate", "")
                        sdp_mid = msg.get("sdpMid", "")
                        sdp_mline_index = msg.get("sdpMLineIndex", 0)

                        if candidate_str:
                            # aiortc expects candidate_from_sdp
                            from aiortc.sdp import candidate_from_sdp

                            candidate = candidate_from_sdp(candidate_str)
                            candidate.sdpMid = sdp_mid
                            candidate.sdpMLineIndex = sdp_mline_index
                            await pc.addIceCandidate(candidate)

            except WebSocketDisconnect:
                logger.info("WebSocket signaling client disconnected")
                await pc.close()
                self._peer_connections.discard(pc)

        return app

    def run(self) -> None:
        """Start the uvicorn server with SSL. Blocks until shutdown."""
        import uvicorn

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(self._cert_path, self._key_path)

        config = uvicorn.Config(
            app=self._app,
            host=self._host,
            port=self._port,
            ssl_certfile=self._cert_path,
            ssl_keyfile=self._key_path,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._server.run()

    async def _close_all_connections(self) -> None:
        """Close all active peer connections."""
        coros = [pc.close() for pc in self._peer_connections]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
        self._peer_connections.clear()

    def shutdown(self) -> None:
        """Shut down the server and close all peer connections."""
        # Close peer connections
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._close_all_connections())
            loop.close()
        except Exception:
            pass

        # Signal uvicorn to stop
        if self._server is not None:
            self._server.should_exit = True
