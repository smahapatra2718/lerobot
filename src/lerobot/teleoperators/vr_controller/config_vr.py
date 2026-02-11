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

from dataclasses import dataclass, field

from ..config import TeleoperatorConfig


@TeleoperatorConfig.register_subclass("vr_controller")
@dataclass
class VRControllerConfig(TeleoperatorConfig):
    host: str = "0.0.0.0"
    port: int = 8443
    ssl_cert_path: str | None = None
    ssl_key_path: str | None = None
    camera_names: list[str] = field(default_factory=lambda: ["left_wrist", "right_wrist", "left_exo"])
    video_width: int = 640
    video_height: int = 480
    video_fps: int = 30
    position_scale: float = 1.0
