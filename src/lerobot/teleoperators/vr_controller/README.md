# VR Teleoperation via WebXR + WebRTC

Real-time bimanual robot control using a Meta Quest Pro headset. Two controllers provide 6DOF poses for controlling left/right SO-100 arms, while robot cameras stream back to the headset via WebRTC. The system runs entirely in the Quest's browser (zero-install on headset).

## Architecture

```
Quest Pro Browser (WebXR)              Python Server
┌────────────────────────┐            ┌──────────────────────┐
│  WebXR immersive-vr    │            │  FastAPI + aiortc     │
│                        │  WebRTC    │                       │
│  ← 3 video tracks ←───│◄───────────│  CameraVideoTrack x3  │
│  → data channel ──────►│───────────►│  (left_wrist,         │
│    (controller poses)  │            │   right_wrist,        │
│                        │            │   left_exo)           │
│                        │            │                       │
│  WebSocket signaling   │◄──────────►│  SDP/ICE exchange     │
└────────────────────────┘            └──────────────────────┘
```

- **Controller poses** are sent from the Quest to the server over a WebRTC data channel at display refresh rate (~72-90Hz)
- **Camera feeds** are sent from the server to the Quest as WebRTC video tracks
- **WebSocket** is used only for initial SDP/ICE signaling
- **HTTPS** is required because WebXR mandates a secure context

## Installation

```bash
pip install lerobot[vr_controller]
```

This installs `aiortc` (WebRTC) and `cryptography` (SSL cert generation). `fastapi` and `uvicorn` are already core dependencies.

## Quick Start

### 1. Start teleoperation

```bash
lerobot-teleoperate \
  --robot.type=bi_so_follower \
  --robot.left_arm_config.port=/dev/ttyUSB0 \
  --robot.right_arm_config.port=/dev/ttyUSB1 \
  --robot.left_arm_config.cameras='{
    wrist: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30},
    exo: {type: opencv, index_or_path: 2, width: 640, height: 480, fps: 30},
  }' \
  --robot.right_arm_config.cameras='{
    wrist: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30},
  }' \
  --teleop.type=vr_controller \
  --teleop.port=8443
```

The server prints a URL like:

```
============================================================
VR Teleop Server Started
============================================================
Open on Quest Pro: https://192.168.1.42:8443
(Accept the self-signed certificate warning)
============================================================
```

### 2. Connect the Quest Pro

1. Open the Quest Pro browser
2. Navigate to the URL printed by the server
3. Accept the self-signed certificate warning
4. Click **Enter VR**

### 3. Calibrate

1. Hold both controllers in a comfortable neutral position
2. Squeeze **both grip buttons** simultaneously
3. The server prints "Calibration done!"

### 4. Control

- **Squeeze both grips** to enable teleoperation
- **Release grips** to pause (robot holds position)
- **Squeeze again** to resume (position re-zeros to current hand position)
- **Triggers** (index finger) control the grippers (analog 0-1)

## USB-C Connection (Recommended)

If your WiFi has client isolation, firewalls, or high latency, connect via USB-C using ADB reverse port forwarding:

```bash
# Install ADB
sudo apt install android-tools-adb

# Enable Quest developer mode:
#   1. Create a Meta developer account at developer.oculus.com
#   2. Meta Quest phone app → Devices → Developer Mode → toggle on
#   3. Plug in USB-C, accept "Allow USB Debugging" on the headset

# Verify connection
adb devices

# Forward port: Quest localhost:8443 → PC localhost:8443
adb reverse tcp:8443 tcp:8443

# Start teleoperation as normal
lerobot-teleoperate --teleop.type=vr_controller --teleop.port=8443 ...
```

Then open **`https://localhost:8443`** on the Quest browser instead of the IP address.

Benefits over WiFi:
- No network configuration issues
- Lower latency for the WebRTC data channel
- Works in any network environment

## Configuration

All options are set via `--teleop.*` flags:

| Flag | Default | Description |
|------|---------|-------------|
| `type` | — | Must be `vr_controller` |
| `host` | `0.0.0.0` | Server bind address |
| `port` | `8443` | HTTPS port |
| `ssl_cert_path` | auto-generated | Path to custom SSL certificate |
| `ssl_key_path` | auto-generated | Path to custom SSL private key |
| `camera_names` | `[left_wrist, right_wrist, left_exo]` | Camera names to stream to headset (see note below) |
| `video_width` | `640` | Video stream width in pixels |
| `video_height` | `480` | Video stream height in pixels |
| `video_fps` | `30` | Video stream frame rate |
| `position_scale` | `1.0` | Multiplier for hand movement magnitude |

### Camera Names

`bi_so_follower` does not have a top-level camera config — all cameras are defined per-arm and automatically prefixed with `left_` or `right_`. For example, a camera named `exo` under `left_arm_config.cameras` becomes `left_exo` in the robot's camera dict. The `camera_names` list in the VR config must match these prefixed names.

| Robot Config | Resulting Name |
|---|---|
| `left_arm_config.cameras.wrist` | `left_wrist` |
| `right_arm_config.cameras.wrist` | `right_wrist` |
| `left_arm_config.cameras.exo` | `left_exo` |

If you only have two cameras, override the default:

```bash
--teleop.camera_names='[left_wrist, right_wrist]'
```

Any camera name in the list that doesn't have a matching device will show a black frame in the headset.

## Controller Mapping

### Meta Quest Pro Controllers

| Input | Action |
|-------|--------|
| Left grip (squeeze) | Enable left arm (both grips must be held) |
| Right grip (squeeze) | Enable right arm (both grips must be held) |
| Left trigger (index) | Left gripper open/close (analog 0.0 - 1.0) |
| Right trigger (index) | Right gripper open/close (analog 0.0 - 1.0) |
| Thumbsticks | Reserved for future use |
| A/B buttons | Reserved for future use |

### Coordinate System

WebXR uses a Y-up, -Z-forward coordinate system. The processor maps this to the robot frame:

| WebXR Axis | Robot Axis |
|------------|------------|
| -Z (forward) | X (forward) |
| X (right) | Y (left) |
| Y (up) | Z (up) |

## VR Display Layout

Three camera feeds are rendered as floating panels in VR space:

```
           ┌──────────┐
           │ left_exo │    ← top-center, overhead view
           │(0,2,-1.5)│
           └──────────┘

  ┌─────────┐           ┌─────────┐
  │  left   │           │  right  │
  │  wrist  │           │  wrist  │
  │(-0.8,1.2,-1.5)│     │(0.8,1.2,-1.5)│
  └─────────┘           └─────────┘
```

## Processor Pipeline

For bimanual VR control, the full action processing chain is:

```
VRController.get_action()
  → {vr.left.pos, vr.left.rot, vr.left.grip, vr.right.*, vr.head.*, vr.enabled}

MapVRActionToRobotAction
  → {left_enabled, left_target_x/y/z, left_target_wx/wy/wz, left_gripper_vel,
     right_enabled, right_target_x/y/z, ...}

Per-arm EEReferenceAndDelta
  → {left_ee.x/y/z/wx/wy/wz/gripper_vel, right_ee.*}

Per-arm EEBoundsAndSafety
  → clips to workspace bounds

Per-arm GripperVelocityToJoint
  → converts gripper_vel to ee.gripper_pos

Per-arm InverseKinematicsEEToJoints
  → {left_shoulder_pan.pos, ..., left_gripper.pos, right_shoulder_pan.pos, ...}

BiSOFollower.send_action()
  → strips left_/right_ prefixes, sends to respective arms
```

The `MapVRActionToRobotAction` processor is registered as `"map_vr_action_to_robot_action"` and supports both bimanual mode (prefixed outputs) and single-arm mode (unprefixed).

## SSL Certificates

On first run, a self-signed certificate is auto-generated at `~/.cache/lerobot/ssl/` with:
- RSA 2048-bit key
- 1-year validity
- All local IP addresses in the Subject Alternative Name (SAN)
- `localhost` in the SAN

The certificate is reused on subsequent runs until it expires. To force regeneration, delete the files:

```bash
rm ~/.cache/lerobot/ssl/vr_cert.pem ~/.cache/lerobot/ssl/vr_key.pem
```

To use your own certificate:

```bash
lerobot-teleoperate \
  --teleop.type=vr_controller \
  --teleop.ssl_cert_path=/path/to/cert.pem \
  --teleop.ssl_key_path=/path/to/key.pem \
  ...
```

## Data Channel Protocol

Controller state is sent as JSON over the WebRTC data channel (unreliable, unordered for lowest latency):

```json
{
  "type": "controller_state",
  "timestamp": 1234567890.123,
  "left": {
    "position": [x, y, z],
    "orientation": [x, y, z, w],
    "trigger": 0.0,
    "grip": 0.0,
    "thumbstick": [x, y],
    "buttons": {"a": false, "b": false}
  },
  "right": { "..." : "same as left" },
  "head": {
    "position": [x, y, z],
    "orientation": [x, y, z, w]
  }
}
```

Quaternions use `[x, y, z, w]` format, matching both WebXR's `XRRigidTransform.orientation` and `Rotation.from_quat()`.

## File Structure

```
src/lerobot/teleoperators/vr_controller/
├── __init__.py          # Exports VRControllerConfig, VRController
├── config_vr.py         # Config dataclass (registered as "vr_controller")
├── ssl_utils.py         # Self-signed SSL certificate generation
├── vr_server.py         # FastAPI + aiortc WebRTC server
├── teleop_vr.py         # VRController(Teleoperator) main class
├── vr_processor.py      # MapVRActionToRobotAction processor step
├── static/
│   ├── index.html       # WebXR entry page
│   └── vr_client.js     # WebXR + WebRTC client (~300 lines)
└── README.md
```

## Troubleshooting

**"WebXR not available" on the Quest browser**
- WebXR requires HTTPS. Make sure you're using `https://` not `http://`
- If using USB-C, make sure `adb reverse` is active

**Certificate warning won't go away**
- This is expected for self-signed certs. Click "Advanced" → "Proceed" (or equivalent)
- On Quest, you may need to accept it twice (once for the page, once for the WebSocket)

**No video feeds in VR**
- Check that the camera names in `--teleop.camera_names` match the robot's camera configuration
- Verify cameras work independently: `lerobot-find-cameras`

**High latency**
- Use USB-C connection instead of WiFi
- Reduce video resolution: `--teleop.video_width=320 --teleop.video_height=240`
- Reduce video FPS: `--teleop.video_fps=15`

**Controllers not detected**
- Make sure both Quest Pro controllers are powered on and paired
- The WebXR session needs `immersive-vr` — some browser versions require a user gesture (the "Enter VR" button handles this)

**"Module not found: aiortc"**
- Install the optional dependencies: `pip install lerobot[vr_controller]`
