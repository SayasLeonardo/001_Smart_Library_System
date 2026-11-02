"""Local face-recognition station. Attendance is recorded only after confirmation."""

import atexit
import copy
import os
import threading
import time
from pathlib import Path

import cv2
from flask import Flask, Response, abort, jsonify, request, send_from_directory

from attendance import SmartLibraryApi
from camera import Camera
from face_recognition import FaceRecognizer
from models import ensure_models


MODULE_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIRECTORY.parent
app = Flask(__name__, static_folder=None)
STATE_LOCK = threading.Lock()
FRAME_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
LATEST_JPEG = b""
PENDING_AT = 0.0
CAMERA_THREAD = None
STATE = {
    "camera_status": "starting",
    "face_status": "ready",
    "selected_action": None,
    "pending_person": None,
    "result": None,
    "message": "Starting the face recognition station.",
}


def update_state(**values):
    with STATE_LOCK:
        STATE.update(values)


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


def camera_worker():
    global LATEST_JPEG, PENDING_AT
    camera = None
    try:
        api_url = os.environ.get("SMART_LIBRARY_API_URL", "https://final-the-smart-library-system.onrender.com")
        api_key = os.environ.get("SMART_LIBRARY_IOT_KEY", "4dd067e0d7f1b713db2614029ed2a3a0")
        if not api_key:
            raise RuntimeError("SMART_LIBRARY_IOT_KEY is not configured for this station.")

        models = ensure_models()
        recognizer = FaceRecognizer(
            models["face_detection_yunet_2023mar.onnx"],
            models["face_recognition_sface_2021dec.onnx"],
        )
        library_api = SmartLibraryApi(api_url, api_key)
        camera = Camera(int(os.environ.get("SMART_LIBRARY_CAMERA", "0")))
        people = []
        active_person_id = None
        last_person_seen = 0.0
        no_face_since = None
        frame_number = 0
        next_sync = 0.0
        camera_was_unavailable = False
        update_state(camera_status="active", message="Camera ready. Select Time-in or Time-out.")

        while not STOP_EVENT.is_set():
            now = time.monotonic()
            if now >= next_sync:
                try:
                    library_api.register_pending_faces(recognizer)
                    people = library_api.get_face_templates()
                    update_state(camera_status="active")
                except RuntimeError as error:
                    update_state(camera_status="backend_error", message=str(error))
                next_sync = now + 30

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
            faces = recognizer.detector.detect(frame)
            no_face_since = None if faces else (no_face_since if no_face_since is not None else now)
            if not faces and no_face_since is not None and now - no_face_since >= 1.5:
                active_person_id = None

            if len(faces) == 1:
                face = faces[0]
                x, y, width, height = (int(value) for value in face[:4])
                cv2.rectangle(frame, (x, y), (x + width, y + height), (78, 132, 91), 2)
                if frame_number % 5 == 0:
                    try:
                        embedding = recognizer.encode(frame)
                        person, score = recognizer.find_match(embedding, people)
                        if person:
                            label = f"{person['name']} ({score:.2f})"
                            cv2.putText(frame, label, (20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 248, 230), 2)
                            if active_person_id != person["id"] or now - last_person_seen >= 3:
                                with STATE_LOCK:
                                    STATE["pending_person"] = dict(person)
                                    STATE["face_status"] = "recognized"
                                    STATE["result"] = None
                                    STATE["message"] = "Face recognized. Choose an attendance action."
                                    PENDING_AT = now
                                active_person_id = person["id"]
                            last_person_seen = now
                        else:
                            cv2.putText(frame, "Face not recognized", (20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 245), 2)
                            with STATE_LOCK:
                                STATE["pending_person"] = None
                                STATE["face_status"] = "unknown"
                                STATE["message"] = "No registered person was found. Please contact the library administrator."
                            active_person_id = None
                    except ValueError:
                        cv2.putText(frame, "Face not clear", (20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 180, 245), 2)
            elif len(faces) > 1:
                cv2.putText(frame, "One person at a time", (20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 180, 245), 2)
                with STATE_LOCK:
                    STATE["pending_person"] = None
                    STATE["face_status"] = "multiple_faces"
                    STATE["message"] = "Please have one person stand in front of the camera."
                active_person_id = None
            elif no_face_since is not None and now - no_face_since >= 1.5:
                with STATE_LOCK:
                    if STATE["pending_person"] and now - PENDING_AT > 20:
                        STATE["pending_person"] = None
                        STATE["face_status"] = "ready"
                        STATE["message"] = "Ready. Select Time-in or Time-out, then face the camera."
                    elif STATE["face_status"] not in ("recorded", "unknown"):
                        STATE["face_status"] = "ready"
                        STATE["message"] = "Camera ready. Select Time-in or Time-out."

            success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if success:
                with FRAME_LOCK:
                    LATEST_JPEG = encoded.tobytes()
    except Exception as error:
        update_state(camera_status="error", face_status="ready", message=f"Station startup failed: {error}")
        app.logger.exception("Face-recognition camera stopped")
    finally:
        if camera:
            camera.close()
        update_state(camera_status="stopped")


def station_state():
    with STATE_LOCK:
        result = copy.deepcopy(STATE)
        pending_at = PENDING_AT
    person = result["pending_person"]
    action = result["selected_action"]
    result["can_confirm"] = bool(person and pending_at and time.monotonic() - pending_at <= 20)
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
        while not STOP_EVENT.is_set():
            with FRAME_LOCK:
                frame = LATEST_JPEG
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            else:
                time.sleep(0.1)

    return Response(stream_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


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
    with STATE_LOCK:
        person = copy.deepcopy(STATE["pending_person"])
        action = STATE["selected_action"]
        pending_at = PENDING_AT
    if not person or not pending_at or time.monotonic() - pending_at > 20:
        return jsonify({"error": "No current face recognition is waiting for confirmation."}), 409
    message, can_record = selected_action_message(person, action)
    if not can_record:
        return jsonify({"error": message}), 409

    try:
        library_api = SmartLibraryApi(
            os.environ.get("SMART_LIBRARY_API_URL", "https://final-the-smart-library-system.onrender.com"),
            os.environ.get("SMART_LIBRARY_IOT_KEY", "4dd067e0d7f1b713db2614029ed2a3a0"),
        )
        saved = library_api.record_attendance(person, action.replace("_", "-"))
    except (RuntimeError, ValueError) as error:
        with STATE_LOCK:
            STATE["face_status"] = "error"
            STATE["message"] = str(error)
        return jsonify({"error": str(error)}), 409

    action_label = "time_in" if saved["event"] == "time_in" else "time_out"
    person["is_inside"] = action_label == "time_in"
    with STATE_LOCK:
        STATE["pending_person"] = None
        STATE["selected_action"] = None
        STATE["face_status"] = "recorded"
        STATE["result"] = {
            "action": action_label,
            "person": saved["person"],
            "timestamp": saved["timestamp"],
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
