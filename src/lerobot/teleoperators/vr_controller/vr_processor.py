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

from lerobot.configs.types import FeatureType, PipelineFeatureType, PolicyFeature
from lerobot.processor import ProcessorStepRegistry, RobotAction, RobotActionProcessorStep

# Target output keys per hand
_TARGET_KEYS = ["enabled", "target_x", "target_y", "target_z", "target_wx", "target_wy", "target_wz", "gripper_vel"]


@ProcessorStepRegistry.register("map_vr_action_to_robot_action")
@dataclass
class MapVRActionToRobotAction(RobotActionProcessorStep):
    """Maps calibrated VR controller poses to standardized robot action inputs.

    This processor step bridges the VR teleoperator's output and the robot's
    expected action format. It remaps each hand's 6-DoF pose (position and
    rotation) to target end-effector pose values, applying axis mapping from
    WebXR's coordinate system (Y-up, -Z-forward) to the robot frame.

    In bimanual mode (default), outputs are prefixed with ``left_`` and
    ``right_``.  In single-arm mode, outputs are unprefixed.

    The output feeds directly into the existing SO-100 IK pipeline:
    ``EEReferenceAndDelta`` -> ``EEBoundsAndSafety`` -> ``GripperVelocityToJoint``
    -> ``InverseKinematicsEEToJoints``
    """

    bimanual: bool = True
    _enabled_prev: bool = field(default=False, init=False, repr=False)

    def action(self, action: RobotAction) -> RobotAction:
        # Pop VR-specific keys
        enabled = bool(action.pop("vr.enabled", False))
        left_pos = action.pop("vr.left.pos", None)
        left_rot = action.pop("vr.left.rot", None)
        left_grip = float(action.pop("vr.left.grip", 0.0))
        right_pos = action.pop("vr.right.pos", None)
        right_rot = action.pop("vr.right.rot", None)
        right_grip = float(action.pop("vr.right.grip", 0.0))
        action.pop("vr.head.pos", None)
        action.pop("vr.head.rot", None)

        if left_pos is None or left_rot is None or right_pos is None or right_rot is None:
            # No data yet — output zeros
            if self.bimanual:
                for prefix in ("left_", "right_"):
                    for key in _TARGET_KEYS:
                        action[f"{prefix}{key}"] = 0.0
            else:
                for key in _TARGET_KEYS:
                    action[key] = 0.0
            return action

        # Convert rotations to rotvec
        left_rotvec = left_rot.as_rotvec()
        right_rotvec = right_rot.as_rotvec()

        # Axis mapping: WebXR Y-up/-Z-forward -> Robot frame
        # VR -Z -> Robot X (forward), VR X -> Robot Y (left), VR Y -> Robot Z (up)
        def _map_hand(pos, rotvec, grip, prefix: str) -> None:
            action[f"{prefix}enabled"] = enabled
            action[f"{prefix}target_x"] = -pos[2] if enabled else 0.0  # VR -Z -> Robot X
            action[f"{prefix}target_y"] = pos[0] if enabled else 0.0   # VR  X -> Robot Y
            action[f"{prefix}target_z"] = pos[1] if enabled else 0.0   # VR  Y -> Robot Z
            action[f"{prefix}target_wx"] = -rotvec[2] if enabled else 0.0
            action[f"{prefix}target_wy"] = rotvec[0] if enabled else 0.0
            action[f"{prefix}target_wz"] = rotvec[1] if enabled else 0.0
            action[f"{prefix}gripper_vel"] = grip  # Always send gripper

        if self.bimanual:
            _map_hand(left_pos, left_rotvec, left_grip, "left_")
            _map_hand(right_pos, right_rotvec, right_grip, "right_")
        else:
            # Single arm mode uses right controller, unprefixed
            _map_hand(right_pos, right_rotvec, right_grip, "")

        self._enabled_prev = enabled
        return action

    def transform_features(
        self, features: dict[PipelineFeatureType, dict[str, PolicyFeature]]
    ) -> dict[PipelineFeatureType, dict[str, PolicyFeature]]:
        # Remove VR-specific features
        for feat in [
            "vr.left.pos", "vr.left.rot", "vr.left.grip",
            "vr.right.pos", "vr.right.rot", "vr.right.grip",
            "vr.head.pos", "vr.head.rot", "vr.enabled",
        ]:
            features[PipelineFeatureType.ACTION].pop(feat, None)

        # Add target features
        if self.bimanual:
            for prefix in ("left_", "right_"):
                for key in _TARGET_KEYS:
                    features[PipelineFeatureType.ACTION][f"{prefix}{key}"] = PolicyFeature(
                        type=FeatureType.ACTION, shape=(1,)
                    )
        else:
            for key in _TARGET_KEYS:
                features[PipelineFeatureType.ACTION][key] = PolicyFeature(
                    type=FeatureType.ACTION, shape=(1,)
                )

        return features
