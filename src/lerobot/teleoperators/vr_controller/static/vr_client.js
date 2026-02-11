/**
 * LeRobot VR Teleop Client
 *
 * WebXR + WebRTC client for Meta Quest Pro.
 * Connects via WebSocket signaling, sends controller poses over a data channel,
 * and renders incoming camera video tracks as textured quads in VR space.
 */

// ---------------------------------------------------------------------------
// Signaling & WebRTC
// ---------------------------------------------------------------------------

class SignalingClient {
    constructor() {
        this.pc = null;
        this.dataChannel = null;
        this.ws = null;
        this.videoElements = [];     // HTMLVideoElement per incoming track
        this.videoTextures = [];     // will be filled by VRTeleop after GL init
        this.onConnected = null;     // callback
        this.onDisconnected = null;
    }

    async connect() {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${proto}//${location.host}/ws/signaling`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this._setupPeerConnection();
        };
        this.ws.onmessage = (evt) => this._onSignalingMessage(JSON.parse(evt.data));
        this.ws.onclose = () => {
            setStatus("WebSocket disconnected");
            if (this.onDisconnected) this.onDisconnected();
        };
        this.ws.onerror = (e) => setStatus("WebSocket error — check server");
    }

    _setupPeerConnection() {
        this.pc = new RTCPeerConnection({
            iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });

        // Data channel for controller poses (unreliable for lowest latency)
        this.dataChannel = this.pc.createDataChannel("controller_input", {
            ordered: false,
            maxRetransmits: 0,
        });
        this.dataChannel.onopen = () => setStatus("Data channel open");
        this.dataChannel.onclose = () => setStatus("Data channel closed");

        // Incoming video tracks from server
        this.pc.ontrack = (event) => {
            const video = document.createElement("video");
            video.srcObject = new MediaStream([event.track]);
            video.autoplay = true;
            video.playsInline = true;
            video.muted = true;
            video.play().catch(() => {});
            this.videoElements.push(video);
        };

        this.pc.onicecandidate = (event) => {
            if (event.candidate) {
                this.ws.send(JSON.stringify({
                    type: "candidate",
                    candidate: event.candidate.candidate,
                    sdpMid: event.candidate.sdpMid,
                    sdpMLineIndex: event.candidate.sdpMLineIndex,
                }));
            }
        };

        this.pc.onconnectionstatechange = () => {
            setStatus(`WebRTC: ${this.pc.connectionState}`);
            if (this.pc.connectionState === "connected" && this.onConnected) {
                this.onConnected();
            }
        };

        // Create offer (we need to add transceivers first to receive video)
        this.pc.addTransceiver("video", { direction: "recvonly" });
        this.pc.addTransceiver("video", { direction: "recvonly" });
        this.pc.addTransceiver("video", { direction: "recvonly" });

        this.pc.createOffer().then((offer) => {
            this.pc.setLocalDescription(offer);
            this.ws.send(JSON.stringify({ type: offer.type, sdp: offer.sdp }));
        });
    }

    _onSignalingMessage(msg) {
        if (msg.type === "answer") {
            this.pc.setRemoteDescription(new RTCSessionDescription(msg));
        } else if (msg.type === "candidate" && msg.candidate) {
            this.pc.addIceCandidate(new RTCIceCandidate({
                candidate: msg.candidate,
                sdpMid: msg.sdpMid,
                sdpMLineIndex: msg.sdpMLineIndex,
            }));
        }
    }

    sendControllerState(data) {
        if (this.dataChannel && this.dataChannel.readyState === "open") {
            this.dataChannel.send(JSON.stringify(data));
        }
    }
}

// ---------------------------------------------------------------------------
// WebGL helpers
// ---------------------------------------------------------------------------

const QUAD_VS = `
attribute vec3 aPosition;
attribute vec2 aUV;
uniform mat4 uMVP;
varying vec2 vUV;
void main() {
    vUV = aUV;
    gl_Position = uMVP * vec4(aPosition, 1.0);
}`;

const QUAD_FS = `
precision mediump float;
varying vec2 vUV;
uniform sampler2D uTexture;
void main() {
    gl_FragColor = texture2D(uTexture, vUV);
}`;

function compileShader(gl, src, type) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.error(gl.getShaderInfoLog(s));
    }
    return s;
}

function createProgram(gl) {
    const prog = gl.createProgram();
    gl.attachShader(prog, compileShader(gl, QUAD_VS, gl.VERTEX_SHADER));
    gl.attachShader(prog, compileShader(gl, QUAD_FS, gl.FRAGMENT_SHADER));
    gl.linkProgram(prog);
    return prog;
}

function createQuadBuffers(gl) {
    // 1x1 quad in XY plane centered at origin
    const pos = new Float32Array([
        -0.5, -0.5, 0,  0.5, -0.5, 0,  0.5, 0.5, 0,
        -0.5, -0.5, 0,  0.5, 0.5, 0,  -0.5, 0.5, 0,
    ]);
    const uv = new Float32Array([
        0, 1,  1, 1,  1, 0,
        0, 1,  1, 0,  0, 0,
    ]);
    const posBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
    gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);

    const uvBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
    gl.bufferData(gl.ARRAY_BUFFER, uv, gl.STATIC_DRAW);

    return { posBuf, uvBuf };
}

// Simple 4x4 matrix helpers (column-major for WebGL)
const mat4 = {
    identity() {
        return new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
    },
    multiply(a, b) {
        const o = new Float32Array(16);
        for (let i = 0; i < 4; i++)
            for (let j = 0; j < 4; j++) {
                let s = 0;
                for (let k = 0; k < 4; k++) s += a[k*4+j] * b[i*4+k];
                o[i*4+j] = s;
            }
        return o;
    },
    translate(m, x, y, z) {
        const t = mat4.identity();
        t[12] = x; t[13] = y; t[14] = z;
        return mat4.multiply(m, t);
    },
    scale(m, sx, sy, sz) {
        const s = mat4.identity();
        s[0] = sx; s[5] = sy; s[10] = sz;
        return mat4.multiply(m, s);
    },
};

// ---------------------------------------------------------------------------
// VR Teleop
// ---------------------------------------------------------------------------

class VRTeleop {
    constructor(signalingClient) {
        this.signaling = signalingClient;
        this.xrSession = null;
        this.xrRefSpace = null;
        this.gl = null;
        this.program = null;
        this.quadBuffers = null;
        this.textures = [];  // WebGL textures for video feeds
    }

    async startVR() {
        if (!navigator.xr) {
            setStatus("WebXR not supported");
            return;
        }

        const supported = await navigator.xr.isSessionSupported("immersive-vr");
        if (!supported) {
            setStatus("immersive-vr not supported");
            return;
        }

        const canvas = document.getElementById("gl-canvas");
        this.gl = canvas.getContext("webgl", { xrCompatible: true });

        // Init shader program and buffers
        this.program = createProgram(this.gl);
        this.quadBuffers = createQuadBuffers(this.gl);

        // Create textures for up to 3 video feeds
        for (let i = 0; i < 3; i++) {
            const tex = this.gl.createTexture();
            this.gl.bindTexture(this.gl.TEXTURE_2D, tex);
            this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_WRAP_S, this.gl.CLAMP_TO_EDGE);
            this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_WRAP_T, this.gl.CLAMP_TO_EDGE);
            this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_MIN_FILTER, this.gl.LINEAR);
            this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_MAG_FILTER, this.gl.LINEAR);
            // Init with 1x1 black pixel
            this.gl.texImage2D(this.gl.TEXTURE_2D, 0, this.gl.RGBA, 1, 1, 0,
                this.gl.RGBA, this.gl.UNSIGNED_BYTE, new Uint8Array([0,0,0,255]));
            this.textures.push(tex);
        }

        this.xrSession = await navigator.xr.requestSession("immersive-vr", {
            requiredFeatures: ["local-floor"],
        });

        this.xrSession.updateRenderState({
            baseLayer: new XRWebGLLayer(this.xrSession, this.gl),
        });

        this.xrRefSpace = await this.xrSession.requestReferenceSpace("local-floor");

        this.xrSession.requestAnimationFrame((t, f) => this.onXRFrame(t, f));
        setStatus("VR session active");
    }

    onXRFrame(time, frame) {
        const session = frame.session;
        session.requestAnimationFrame((t, f) => this.onXRFrame(t, f));

        const gl = this.gl;
        const glLayer = session.renderState.baseLayer;
        gl.bindFramebuffer(gl.FRAMEBUFFER, glLayer.framebuffer);

        // Gather controller & head data
        const controllerData = this._gatherControllerData(frame);

        // Send controller state
        if (controllerData) {
            this.signaling.sendControllerState(controllerData);
        }

        // Update video textures from HTMLVideoElements
        const videos = this.signaling.videoElements;
        for (let i = 0; i < Math.min(videos.length, this.textures.length); i++) {
            const video = videos[i];
            if (video.readyState >= video.HAVE_CURRENT_DATA) {
                gl.bindTexture(gl.TEXTURE_2D, this.textures[i]);
                gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
            }
        }

        // Render for each eye
        const pose = frame.getViewerPose(this.xrRefSpace);
        if (!pose) return;

        gl.clearColor(0.05, 0.05, 0.1, 1.0);

        for (const view of pose.views) {
            const vp = glLayer.getViewport(view);
            gl.viewport(vp.x, vp.y, vp.width, vp.height);
            gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
            gl.enable(gl.DEPTH_TEST);

            const viewProj = mat4.multiply(
                new Float32Array(view.projectionMatrix),
                new Float32Array(view.transform.inverse.matrix)
            );

            this._renderVideoQuads(gl, viewProj);
        }
    }

    _gatherControllerData(frame) {
        const session = frame.session;
        const data = {
            type: "controller_state",
            timestamp: performance.now() / 1000.0,
            left: null,
            right: null,
            head: null,
        };

        // Head pose
        const viewerPose = frame.getViewerPose(this.xrRefSpace);
        if (viewerPose) {
            const headPos = viewerPose.transform.position;
            const headOri = viewerPose.transform.orientation;
            data.head = {
                position: [headPos.x, headPos.y, headPos.z],
                orientation: [headOri.x, headOri.y, headOri.z, headOri.w],
            };
        }

        // Controllers
        for (const source of session.inputSources) {
            if (!source.gripSpace || !source.gamepad) continue;

            const gripPose = frame.getPose(source.gripSpace, this.xrRefSpace);
            if (!gripPose) continue;

            const pos = gripPose.transform.position;
            const ori = gripPose.transform.orientation;
            const gp = source.gamepad;

            const hand = {
                position: [pos.x, pos.y, pos.z],
                orientation: [ori.x, ori.y, ori.z, ori.w],
                trigger: gp.buttons[0] ? gp.buttons[0].value : 0,
                grip: gp.buttons[1] ? gp.buttons[1].value : 0,
                thumbstick: gp.axes.length >= 4 ? [gp.axes[2], gp.axes[3]] : [0, 0],
                buttons: {
                    a: gp.buttons[4] ? gp.buttons[4].pressed : false,
                    b: gp.buttons[5] ? gp.buttons[5].pressed : false,
                },
            };

            if (source.handedness === "left") {
                data.left = hand;
            } else if (source.handedness === "right") {
                data.right = hand;
            }
        }

        // Only send if we have at least one controller
        if (data.left || data.right) {
            // Fill missing hand with zeros
            const emptyHand = {
                position: [0, 0, 0],
                orientation: [0, 0, 0, 1],
                trigger: 0, grip: 0,
                thumbstick: [0, 0],
                buttons: { a: false, b: false },
            };
            if (!data.left) data.left = emptyHand;
            if (!data.right) data.right = emptyHand;
            if (!data.head) data.head = { position: [0, 0, 0], orientation: [0, 0, 0, 1] };
            return data;
        }
        return null;
    }

    _renderVideoQuads(gl, viewProj) {
        const prog = this.program;
        gl.useProgram(prog);

        const aPos = gl.getAttribLocation(prog, "aPosition");
        const aUV = gl.getAttribLocation(prog, "aUV");
        const uMVP = gl.getUniformLocation(prog, "uMVP");
        const uTex = gl.getUniformLocation(prog, "uTexture");

        gl.enableVertexAttribArray(aPos);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuffers.posBuf);
        gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);

        gl.enableVertexAttribArray(aUV);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuffers.uvBuf);
        gl.vertexAttribPointer(aUV, 2, gl.FLOAT, false, 0, 0);

        // Quad placements in VR space:
        //   [0] left wrist  → left of user   (-0.8, 1.2, -1.5)
        //   [1] right wrist → right of user  ( 0.8, 1.2, -1.5)
        //   [2] exo (top)   → above center   ( 0.0, 2.0, -1.5)
        const placements = [
            { x: -0.8, y: 1.2, z: -1.5, sx: 0.6, sy: 0.45 },
            { x:  0.8, y: 1.2, z: -1.5, sx: 0.6, sy: 0.45 },
            { x:  0.0, y: 2.0, z: -1.5, sx: 0.6, sy: 0.45 },
        ];

        for (let i = 0; i < Math.min(this.textures.length, placements.length); i++) {
            const p = placements[i];
            let model = mat4.identity();
            model = mat4.translate(model, p.x, p.y, p.z);
            model = mat4.scale(model, p.sx, p.sy, 1.0);

            const mvp = mat4.multiply(viewProj, model);
            gl.uniformMatrix4fv(uMVP, false, mvp);

            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, this.textures[i]);
            gl.uniform1i(uTex, 0);

            gl.drawArrays(gl.TRIANGLES, 0, 6);
        }

        gl.disableVertexAttribArray(aPos);
        gl.disableVertexAttribArray(aUV);
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function setStatus(msg) {
    const el = document.getElementById("status");
    if (el) el.textContent = msg;
    console.log("[VR Teleop]", msg);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

(function main() {
    const signaling = new SignalingClient();
    const vrTeleop = new VRTeleop(signaling);

    const enterBtn = document.getElementById("enter-vr");

    signaling.onConnected = () => {
        setStatus("Connected — ready to enter VR");
        enterBtn.disabled = false;
    };

    enterBtn.addEventListener("click", () => {
        vrTeleop.startVR();
        enterBtn.disabled = true;
    });

    // Check WebXR support
    if (navigator.xr) {
        navigator.xr.isSessionSupported("immersive-vr").then((ok) => {
            if (!ok) setStatus("immersive-vr not supported on this device");
        });
    } else {
        setStatus("WebXR not available (need HTTPS + compatible browser)");
    }

    setStatus("Connecting to server...");
    signaling.connect();
})();
