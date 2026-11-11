"""Local face-recognition station. Attendance is recorded only after confirmation."""

import atexit
import copy
import os
import threading
import time
import unicodedata
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, request, send_from_directory

from attendance import SmartLibraryApi
from camera import Camera
from face_recognition import FaceRecognizer
from models import ensure_models


MODULE_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIRECTORY.parent
app = Flask(__name__, static_folder=None)
STATE_LOCK = threading.Lock()
FRAME_LOCK = threading.Lock()
CONFIRM_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
LATEST_JPEG = b""
PENDING_AT = 0.0
CAMERA_THREAD = None
LIBRARY_API = None

# Optional automatic attendance (OFF by default; the confirm-first flow is unchanged unless enabled).
# Set SMART_LIBRARY_AUTO_ATTENDANCE=1 on the Raspberry Pi to record Time-in/Time-out as soon as a
# registered, active person is recognized. The server still rejects duplicate Time-ins and
# invalid Time-outs, and a person must leave the camera view before they can be recorded again.
AUTO_ATTENDANCE = os.environ.get("SMART_LIBRARY_AUTO_ATTENDANCE", "0") == "1"
AUTO_COOLDOWN = int(os.environ.get("SMART_LIBRARY_AUTO_COOLDOWN", "60"))  # seconds per person
AUTO_RETRY_SECONDS = 5    # retry delay after a failed automatic save (e.g. network drop)
AUTO_LOCK = threading.Lock()
AUTO_STATE = {"busy": False, "blocked_id": None, "last_at": {}}

CONFIRM_WINDOW = 20       # seconds a recognition stays valid for confirmation
RESET_AFTER_NO_FACE = 5   # seconds without a face before old results are cleared
RETRY_SECONDS = 5         # wait before restarting the station after a failure

# BGR colors for the overlay drawn on the video.
COLOR_BOX = (78, 132, 91)
COLOR_OK = (230, 248, 230)
COLOR_BAD = (120, 120, 245)
COLOR_WARN = (120, 180, 245)

STATE = {
    "camera_status": "starting",
    "face_status": "ready",
    "selected_action": None,
    "pending_person": None,
    "result": None,
    "message": "Starting the face recognition station.",
}


def get_api_settings():
    """Read the Smart Library API URL and IoT key from environment variables only."""
    url = os.environ.get("SMART_LIBRARY_API_URL")
    key = os.environ.get("SMART_LIBRARY_IOT_KEY")
    if not url or not key:
        raise RuntimeError(
            "Set SMART_LIBRARY_API_URL and SMART_LIBRARY_IOT_KEY on this station."
        )
    return url, key


def update_state(**values):
    with STATE_LOCK:
        STATE.update(values)


def ascii_label(text):
    """cv2.putText only draws ASCII, so names like 'Nino' with a tilde become plain letters."""
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")


def selected_action_message(person, action):
    if not person:
        return "Stand in front of the camera to identify a registered person.", False
    if person.get("status") != "active":
        return f"Inactive person. {person['name']} is not allowed to record library attendance.", False
    if not action:
        return "Face recognized. Select Time-in or Time-out to continue.", False
    if action == "time_in" and person["is_inside"]:
        return f"Already Timed-in. {person['name']} is currently inside the library.", False
    if action == "time_out" and not person["is_inside"]:
        return "Time-out cannot be recorded. No active Time-in was found for this person.", False
    return "Confirm the selected attendance action below.", True


def auto_record_attendance(person):
    """Auto mode only: save Time-in or Time-out for a recognized person (runs in a thread)."""
    global PENDING_AT
    person_id = person["id"]
    try:
        library_api = LIBRARY_API
        if library_api is None:
            raise RuntimeError("The station is still starting.")
        if person.get("status") != "active":
            return
        action = "time_out" if person["is_inside"] else "time_in"
        with CONFIRM_LOCK:
            saved = library_api.record_attendance(person, action.replace("_", "-"))
            event = str(saved.get("event", action)).replace("-", "_")
            action_label = "time_in" if event == "time_in" else "time_out"
            library_api.mark_inside(person_id, action_label == "time_in")
            with STATE_LOCK:
                STATE["pending_person"] = None
                STATE["selected_action"] = None
                STATE["face_status"] = "recorded"
                STATE["result"] = {
                    "action": action_label,
                    "person": saved.get("person") or person,
                    "timestamp": saved.get("timestamp", ""),
                }
                STATE["message"] = "Attendance saved to the Smart Library System."
                PENDING_AT = 0.0
    except (RuntimeError, ValueError) as error:
        with STATE_LOCK:
            STATE["face_status"] = "error"
            STATE["message"] = str(error)
        with AUTO_LOCK:  # allow a retry shortly, without hammering the server
            AUTO_STATE["blocked_id"] = None
            AUTO_STATE["last_at"][person_id] = time.monotonic() - AUTO_COOLDOWN + AUTO_RETRY_SECONDS
    finally:
        with AUTO_LOCK:
            AUTO_STATE["busy"] = False


def maybe_auto_record(person, now):
    """Start an automatic save unless one is running, the person was just recorded, or is cooling down."""
    if not AUTO_ATTENDANCE:
        return
    with AUTO_LOCK:
        if AUTO_STATE["busy"] or AUTO_STATE["blocked_id"] == person["id"]:
            return
        if now - AUTO_STATE["last_at"].get(person["id"], -AUTO_COOLDOWN) < AUTO_COOLDOWN:
            return
        AUTO_STATE["busy"] = True
        AUTO_STATE["blocked_id"] = person["id"]
        AUTO_STATE["last_at"][person["id"]] = now
    threading.Thread(
        target=auto_record_attendance, args=(copy.deepcopy(person),), name="auto-attendance", daemon=True
    ).start()


def run_station():
    """One station session. Raises on any failure; camera_worker() restarts it."""
    global LATEST_JPEG, PENDING_AT, LIBRARY_API
    camera = None
    try:
        api_url, api_key = get_api_settings()
        models = ensure_models()
        recognizer = FaceRecognizer(
            models["face_detection_yunet_2023mar.onnx"],
            models["face_recognition_sface_2021dec.onnx"],
        )
        library_api = SmartLibraryApi(api_url, api_key)
        LIBRARY_API = library_api
        camera = Camera(int(os.environ.get("SMART_LIBRARY_CAMERA", "0")))

        people = []
        overlay = None
        active_person_id = None
        last_person_seen = 0.0
        unknown_count = 0
        no_face_since = None
        frame_number = 0
        next_sync = 0.0
        next_refresh = 0.0
        backend_down = False
        camera_was_unavailable = False
        update_state(
            camera_status="active",
            face_status="ready",
            pending_person=None,
            message="Camera ready. Select Time-in or Time-out.",
        )

        while not STOP_EVENT.is_set():
            now = time.monotonic()

            # Every 30 s: start a background sync (enroll new faces, fetch templates).
            if now >= next_sync:
                next_sync = now + 30
                library_api.register_pending_faces(recognizer)

            # Every 2 s: pick up the latest templates the background sync has fetched.
            if now >= next_refresh:
                next_refresh = now + 2
                try:
                    people = library_api.get_face_templates()
                    if backend_down:
                        backend_down = False
                        update_state(camera_status="active", message="Connected to the Smart Library System.")
                except RuntimeError as error:
                    backend_down = True
                    update_state(camera_status="backend_error", message=str(error))

            frame = camera.read()
            if frame is None:
                if not camera_was_unavailable:
                    update_state(
                        camera_status="camera_error",
                        message="The camera stopped returning frames. Attempting to reconnect...",
                    )
                    camera_was_unavailable = True
                time.sleep(0.1)
                continue
            if camera_was_unavailable:
                update_state(camera_status="active", message="Camera reconnected. Select Time-in or Time-out.")
                camera_was_unavailable = False

            frame_number += 1
            faces = recognizer.detector.detect(frame)  # the frame is still unmodified here

            if faces:
                no_face_since = None
            else:
                if no_face_since is None:
                    no_face_since = now
                if now - no_face_since >= 1.5:
                    active_person_id = None
                    unknown_count = 0
                    overlay = None
                    with AUTO_LOCK:  # the person left the view: they can be recorded again later
                        AUTO_STATE["blocked_id"] = None

            if len(faces) == 1:
                if frame_number % 5 == 0:
                    try:
                        embedding = recognizer.encode_face(frame, faces[0])
                        person, score = recognizer.find_match(embedding, people)
                    except ValueError:
                        overlay = ("Face not clear", COLOR_WARN)
                    else:
                        if person:
                            unknown_count = 0
                            overlay = (ascii_label(f"{person['name']} ({score:.2f})"), COLOR_OK)
                            # Never send the face embedding to the browser.
                            public = {key: value for key, value in person.items() if key != "encoding"}
                            with STATE_LOCK:
                                if active_person_id != person["id"] or now - last_person_seen >= 3:
                                    STATE["pending_person"] = public
                                    STATE["face_status"] = "recognized"
                                    STATE["result"] = None
                                    STATE["message"] = (
                                        "Face recognized. Saving attendance..."
                                        if AUTO_ATTENDANCE else "Face recognized. Choose an attendance action."
                                    )
                                    PENDING_AT = now
                                elif STATE["pending_person"] and STATE["pending_person"]["id"] == person["id"]:
                                    PENDING_AT = now  # still in front of the camera: keep it valid
                            maybe_auto_record(public, now)
                            active_person_id = person["id"]
                            last_person_seen = now
                        else:
                            # Require a few misses in a row so one bad frame does not flicker the screen.
                            unknown_count += 1
                            if unknown_count >= 3:
                                overlay = ("Face not recognized", COLOR_BAD)
                                with STATE_LOCK:
                                    STATE["pending_person"] = None
                                    STATE["result"] = None
                                    STATE["face_status"] = "unknown"
                                    STATE["message"] = "No registered person was found. Please contact the library administrator."
                                active_person_id = None
                            else:
                                overlay = ("Checking...", COLOR_WARN)
            elif len(faces) > 1:
                overlay = ("One person at a time", COLOR_WARN)
                unknown_count = 0
                with STATE_LOCK:
                    STATE["pending_person"] = None
                    STATE["result"] = None
                    STATE["face_status"] = "multiple_faces"
                    STATE["message"] = "Please have one person stand in front of the camera."
                active_person_id = None
            elif no_face_since is not None and now - no_face_since >= 1.5:
                idle = now - no_face_since
                with STATE_LOCK:
                    if STATE["pending_person"] and now - PENDING_AT > CONFIRM_WINDOW:
                        STATE["pending_person"] = None
                        STATE["face_status"] = "ready"
                        STATE["message"] = "Ready. Select Time-in or Time-out, then face the camera."
                    elif STATE["face_status"] in ("recorded", "unknown", "error"):
                        if idle >= RESET_AFTER_NO_FACE:
                            STATE["pending_person"] = None
                            STATE["result"] = None
                            STATE["face_status"] = "ready"
                            STATE["message"] = "Camera ready. Select Time-in or Time-out."
                    elif STATE["face_status"] != "ready":
                        STATE["face_status"] = "ready"
                        STATE["message"] = "Camera ready. Select Time-in or Time-out."

            # Draw on the video only after detection and recognition are finished.
            if len(faces) == 1:
                x, y, width, height = (int(value) for value in faces[0][:4])
                cv2.rectangle(frame, (x, y), (x + width, y + height), COLOR_BOX, 2)
            if faces and overlay:
                cv2.putText(frame, overlay[0], (20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, overlay[1], 2)

            success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if success:
                with FRAME_LOCK:
                    LATEST_JPEG = encoded.tobytes()
    finally:
        if camera:
            camera.close()


def camera_worker():
    """Keep the station running: if it fails (camera, models, network), show why and retry."""
    while not STOP_EVENT.is_set():
        try:
            run_station()
        except Exception as error:
            app.logger.exception("Face-recognition station stopped")
            update_state(
                camera_status="error",
                face_status="ready",
                pending_person=None,
                message=f"Station problem: {error} Retrying in {RETRY_SECONDS} seconds...",
            )
        if STOP_EVENT.wait(RETRY_SECONDS):
            break
    update_state(camera_status="stopped")


def station_state():
    with STATE_LOCK:
        result = copy.deepcopy(STATE)
        pending_at = PENDING_AT
    person = result["pending_person"]
    action = result["selected_action"]
    result["can_confirm"] = bool(person and pending_at and time.monotonic() - pending_at <= CONFIRM_WINDOW)
    result["action_message"], action_valid = selected_action_message(person, action)
    result["can_confirm"] = result["can_confirm"] and action_valid
    return result


@app.get("/")
def index():
    return send_from_directory(MODULE_DIRECTORY, "index.html")


@app.get("/style.css")
def station_styles():
    return send_from_directory(MODULE_DIRECTORY, "style.css")


@app.get("/script.js")
def station_script():
    return send_from_directory(MODULE_DIRECTORY, "script.js")


@app.get("/logo")
def station_logo():
    return send_from_directory(PROJECT_ROOT, "ptc_logo.png")


@app.get("/camera_feed")
def camera_feed():
    def stream_frames():
        last_sent = None
        while not STOP_EVENT.is_set():
            with FRAME_LOCK:
                frame = LATEST_JPEG
            if frame and frame is not last_sent:  # only send frames the browser has not seen
                last_sent = frame
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.03)

    response = Response(stream_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/state")
def get_station_state():
    return jsonify(station_state())


@app.post("/api/action")
def choose_action():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("time_in", "time_out"):
        return jsonify({"error": "Select Time-in or Time-out."}), 400
    with STATE_LOCK:
        STATE["selected_action"] = action
        STATE["result"] = None
        STATE["message"] = "Attendance action selected. Confirm after the person is recognized."
    return jsonify(station_state())


@app.post("/api/cancel")
def cancel_pending():
    global PENDING_AT
    with STATE_LOCK:
        STATE["pending_person"] = None
        STATE["result"] = None
        STATE["face_status"] = "ready"
        STATE["message"] = "Recognition cleared. The camera is ready for another person."
        PENDING_AT = 0.0
    return jsonify(station_state())


@app.post("/api/confirm")
def confirm_attendance():
    global PENDING_AT
    library_api = LIBRARY_API
    if library_api is None:
        return jsonify({"error": "The station is still starting. Please wait a moment."}), 409

    # One confirmation at a time, so a double tap can never record attendance twice.
    with CONFIRM_LOCK:
        with STATE_LOCK:
            person = copy.deepcopy(STATE["pending_person"])
            action = STATE["selected_action"]
            pending_at = PENDING_AT
        if not person or not pending_at or time.monotonic() - pending_at > CONFIRM_WINDOW:
            return jsonify({"error": "No current face recognition is waiting for confirmation."}), 409
        message, can_record = selected_action_message(person, action)
        if not can_record:
            return jsonify({"error": message}), 409

        try:
            saved = library_api.record_attendance(person, action.replace("_", "-"))
        except (RuntimeError, ValueError) as error:
            with STATE_LOCK:
                STATE["face_status"] = "error"
                STATE["message"] = str(error)
            return jsonify({"error": str(error)}), 409

        # The API may answer "time-in" or "time_in"; accept both, fall back to what was requested.
        event = str(saved.get("event", action)).replace("-", "_")
        action_label = "time_in" if event == "time_in" else "time_out"

        # Update the cached person right away so the same action is not offered again.
        mark_inside = getattr(library_api, "mark_inside", None)
        if mark_inside:
            mark_inside(person["id"], action_label == "time_in")

        with STATE_LOCK:
            STATE["pending_person"] = None
            STATE["selected_action"] = None
            STATE["face_status"] = "recorded"
            STATE["result"] = {
                "action": action_label,
                "person": saved.get("person") or person,
                "timestamp": saved.get("timestamp", ""),
            }
            STATE["message"] = "Attendance saved to the Smart Library System."
            PENDING_AT = 0.0
    return jsonify(station_state())


def start_camera_thread():
    global CAMERA_THREAD
    CAMERA_THREAD = threading.Thread(target=camera_worker, name="camera-worker", daemon=True)
    CAMERA_THREAD.start()


@atexit.register
def stop_station():
    STOP_EVENT.set()


if __name__ == "__main__":
    start_camera_thread()
    app.run(
        host=os.environ.get("SMART_LIBRARY_STATION_HOST", "127.0.0.1"),
        port=int(os.environ.get("SMART_LIBRARY_STATION_PORT", "5001")),
        debug=False,
        threaded=True,
        use_reloader=False,
    )
