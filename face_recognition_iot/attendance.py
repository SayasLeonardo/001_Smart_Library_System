"""Small HTTP client for the main Smart Library Flask API."""

import json
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np


class SmartLibraryApi:
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._lock = threading.Lock()
        self._worker = None
        self._worker_recognizer = None
        self._templates = []
        self._last_error = None

    def _request(self, path, method="GET", body=None, binary=False, timeout=30):
        headers = {"X-IOT-API-Key": self.api_key}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                result = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Library API returned {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Cannot connect to the Smart Library API: {error.reason}") from error
        except OSError as error:  # read timeouts / dropped connections
            raise RuntimeError(f"Cannot reach the Smart Library API: {error}") from error
        if binary:
            return result
        try:
            return json.loads(result.decode("utf-8"))
        except ValueError as error:
            raise RuntimeError("The Smart Library API returned an unexpected response.") from error

    def register_pending_faces(self, recognizer):
        """Start a background sync and return at once, so the camera loop never waits."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._sync, args=(recognizer,), name="library-sync", daemon=True
            )
            self._worker.start()

    def get_face_templates(self):
        """Return the templates fetched by the background sync (empty until the first sync ends)."""
        with self._lock:
            templates = self._templates
            error = self._last_error
        if error and not templates:
            raise RuntimeError(error)
        return templates

    def mark_inside(self, person_id, inside):
        """Update the cached person right after a successful record, before the next sync."""
        with self._lock:
            for person in self._templates:
                if person["id"] == person_id:
                    person["is_inside"] = inside

    def _sync(self, recognizer):
        try:
            if self._worker_recognizer is None:
                self._worker_recognizer = recognizer.clone()
            self._register_pending(self._worker_recognizer)
        except Exception as error:
            print(f"Pending face registration failed: {error}")
        try:
            templates = self._request("/api/iot/face-templates")["people"]
            with self._lock:
                self._templates = templates
                self._last_error = None
        except Exception as error:
            print(f"Library sync failed: {error}")
            with self._lock:
                self._last_error = str(error)

    def _register_pending(self, recognizer):
        pending = self._request("/api/iot/face-enrollments")["people"]
        for person in pending:
            try:
                image_bytes = self._request(
                    f"/api/iot/people/{person['id']}/face-image", binary=True
                )
                image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    print(f"Skipped invalid face image for {person['school_id']}.")
                    continue
                encoding = recognizer.encode(image)
                self._request(
                    f"/api/iot/people/{person['id']}/face-encoding",
                    method="PUT",
                    body={"encoding": encoding},
                )
                print(f"Face registered for {person['name']} ({person['school_id']}).")
            except ValueError as error:
                print(f"Face registration skipped for {person['school_id']}: {error}")
            except Exception as error:
                print(f"Face registration failed for {person['school_id']}: {error}")

    def record_attendance(self, person, action=None):
        event = action or ("time-out" if person["is_inside"] else "time-in")
        if event not in ("time-in", "time-out"):
            raise ValueError("Attendance action must be time-in or time-out.")
        return self._request(
            f"/api/attendance/{event}",
            method="POST",
            body={"school_id": person["school_id"]},
            timeout=60,
        )
