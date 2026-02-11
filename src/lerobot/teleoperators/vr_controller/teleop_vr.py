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

import logging
import threading
import time
from typing import Any

import numpy as np

from lerobot.teleoperators.teleoperator import Teleoperator
from lerobot.teleoperators.vr_controller.config_vr import VRControllerConfig
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected
from lerobot.utils.rotation import Rotation

logger = logging.getLogger(__name__)


class VRController(Teleoperator):
    """VR-based teleoperator using WebXR on a Meta Quest Pro headset.

    Two controllers provide 6DOF poses for controlling left/right arms, while
    robot cameras stream back to the headset via WebRTC.  The system runs
    entirely in the Quest's browser (zero-install on headset).

    Enable teleoperation by pressing both grip buttons simultaneously. While
    enabled, the first grip press captures a reference pose; when disabled
    and pressed again the position calibration is re-applied.
    """

    config_class = VRControllerConfig
    name = "vr_controller"

    def __init__(self, config: VRControllerConfig):
        super().__init__(config)
        self.config = config

        # Server and threading (VRWebRTCServer imported lazily in connect())
        self._server = None
        self._server_thread: threading.Thread | None = None

        # Shared state between server (WebRTC data channel) and get_action()
        self._state_lock = threading.Lock()
        self._latest_controller_state: dict = {}
        self._camera_frames: dict[str, np.ndarray] = {}

        # Cameras set by the teleop integration
        self._cameras: dict = {}

        # Calibration state per hand
        self._calib_pos: dict[str, np.ndarray] = {}  # "left", "right"
        self._calib_rot_inv: dict[str, Rotation] = {}
        self._enabled: bool = False

    @property
    def action_features(self) -> dict[str, type]:
        return {
            "vr.left.pos": np.ndarray,
            "vr.left.rot": Rotation,
            "vr.left.grip": float,
            "vr.right.pos": np.ndarray,
            "vr.right.rot": Rotation,
            "vr.right.grip": float,
            "vr.head.pos": np.ndarray,
            "vr.head.rot": Rotation,
            "vr.enabled": bool,
        }

    @property
    def feedback_features(self) -> dict[str, type]:
        # Camera frames can be sent as feedback (fallback path)
        return {}

    @property
    def is_connected(self) -> bool:
        return self._server is not None

    @property
    def is_calibrated(self) -> bool:
        return "left" in self._calib_pos and "right" in self._calib_pos

    def set_cameras(self, cameras: dict) -> None:
        """Set robot camera references before connect(). Called from teleop script integration."""
        self._cameras = cameras

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:
        from lerobot.teleoperators.vr_controller.ssl_utils import get_or_create_ssl_context
        from lerobot.teleoperators.vr_controller.vr_server import VRWebRTCServer

        # Generate/load SSL certs
        cert_path, key_path = get_or_create_ssl_context(
            self.config.ssl_cert_path, self.config.ssl_key_path
        )

        # Create server
        self._server = VRWebRTCServer(
            host=self.config.host,
            port=self.config.port,
            cert_path=cert_path,
            key_path=key_path,
            camera_names=self.config.camera_names,
            camera_frames=self._camera_frames,
            shared_state=self._latest_controller_state,
            state_lock=self._state_lock,
            video_width=self.config.video_width,
            video_height=self.config.video_height,
            video_fps=self.config.video_fps,
        )

        # Start server in daemon thread
        self._server_thread = threading.Thread(target=self._server.run, daemon=True)
        self._server_thread.start()

        # Print connection URL
        from lerobot.teleoperators.vr_controller.ssl_utils import _get_local_ips

        ips = _get_local_ips()
        primary_ip = ips[-1] if len(ips) > 1 else ips[0]
        print(f"\n{'=' * 60}")
        print("VR Teleop Server Started")
        print(f"{'=' * 60}")
        print(f"Open on Quest Pro: https://{primary_ip}:{self.config.port}")
        print("(Accept the self-signed certificate warning)")
        print(f"{'=' * 60}\n")

        if calibrate:
            self.calibrate()

    def calibrate(self) -> None:
        """Wait for both grip buttons pressed simultaneously to capture reference pose."""
        print("Calibration: Press and hold BOTH grip buttons on the VR controllers...")
        print("(Make sure both controllers are in a comfortable neutral position)\n")

        while True:
            with self._state_lock:
                state = dict(self._latest_controller_state)

            left = state.get("left", {})
            right = state.get("right", {})
            left_grip = float(left.get("grip", 0.0))
            right_grip = float(right.get("grip", 0.0))

            if left_grip > 0.5 and right_grip > 0.5:
                # Capture reference pose for each hand
                left_pos = np.array(left.get("position", [0, 0, 0]), dtype=float)
                left_quat = np.array(left.get("orientation", [0, 0, 0, 1]), dtype=float)
                right_pos = np.array(right.get("position", [0, 0, 0]), dtype=float)
                right_quat = np.array(right.get("orientation", [0, 0, 0, 1]), dtype=float)

                self._calib_pos["left"] = left_pos.copy()
                self._calib_rot_inv["left"] = Rotation.from_quat(left_quat).inv()
                self._calib_pos["right"] = right_pos.copy()
                self._calib_rot_inv["right"] = Rotation.from_quat(right_quat).inv()
                self._enabled = False
                print("Calibration done!\n")
                return

            time.sleep(0.01)

    def _reapply_position_calibration(self, state: dict) -> None:
        """Re-capture position calibration from current raw poses."""
        left = state.get("left", {})
        right = state.get("right", {})
        left_pos = np.array(left.get("position", [0, 0, 0]), dtype=float)
        right_pos = np.array(right.get("position", [0, 0, 0]), dtype=float)
        self._calib_pos["left"] = left_pos.copy()
        self._calib_pos["right"] = right_pos.copy()

    def configure(self) -> None:
        pass

    @check_if_not_connected
    def get_action(self) -> dict:
        # Update camera frames from robot cameras
        for cam_name, camera in self._cameras.items():
            try:
                frame = camera.read_latest()
                if frame is not None:
                    self._camera_frames[cam_name] = frame
            except Exception:
                pass

        # Read latest controller state
        with self._state_lock:
            state = dict(self._latest_controller_state)

        if not self.is_calibrated or not state:
            return {}

        # Parse left/right/head poses
        left = state.get("left", {})
        right = state.get("right", {})
        head = state.get("head", {})

        left_pos = np.array(left.get("position", [0, 0, 0]), dtype=float)
        left_quat = np.array(left.get("orientation", [0, 0, 0, 1]), dtype=float)
        left_rot = Rotation.from_quat(left_quat)
        left_trigger = float(left.get("trigger", 0.0))
        left_grip = float(left.get("grip", 0.0))

        right_pos = np.array(right.get("position", [0, 0, 0]), dtype=float)
        right_quat = np.array(right.get("orientation", [0, 0, 0, 1]), dtype=float)
        right_rot = Rotation.from_quat(right_quat)
        right_trigger = float(right.get("trigger", 0.0))
        right_grip = float(right.get("grip", 0.0))

        head_pos = np.array(head.get("position", [0, 0, 0]), dtype=float)
        head_quat = np.array(head.get("orientation", [0, 0, 0, 1]), dtype=float)
        head_rot = Rotation.from_quat(head_quat)

        # Enable = both grips > 0.5
        enable = left_grip > 0.5 and right_grip > 0.5

        # Rising edge: re-capture position calibration
        if enable and not self._enabled:
            self._reapply_position_calibration(state)

        self._enabled = enable

        # Apply calibration: calib_rot_inv.apply(raw_pos - calib_pos), calib_rot_inv * raw_rot
        scale = self.config.position_scale

        left_pos_cal = self._calib_rot_inv["left"].apply(left_pos - self._calib_pos["left"]) * scale
        left_rot_cal = self._calib_rot_inv["left"] * left_rot

        right_pos_cal = self._calib_rot_inv["right"].apply(right_pos - self._calib_pos["right"]) * scale
        right_rot_cal = self._calib_rot_inv["right"] * right_rot

        return {
            "vr.left.pos": left_pos_cal,
            "vr.left.rot": left_rot_cal,
            "vr.left.grip": left_trigger,  # Trigger for gripper control
            "vr.right.pos": right_pos_cal,
            "vr.right.rot": right_rot_cal,
            "vr.right.grip": right_trigger,
            "vr.head.pos": head_pos,
            "vr.head.rot": head_rot,
            "vr.enabled": self._enabled,
        }

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        """Accept camera frames from observation dict as fallback path."""
        for cam_name in self.config.camera_names:
            if cam_name in feedback:
                self._camera_frames[cam_name] = feedback[cam_name]

    @check_if_not_connected
    def disconnect(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None

        if self._server_thread is not None and self._server_thread.is_alive():
            self._server_thread.join(timeout=3.0)
            self._server_thread = None
