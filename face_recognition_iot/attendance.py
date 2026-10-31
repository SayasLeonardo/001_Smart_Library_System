"""Small HTTP client for the main Smart Library Flask API."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class SmartLibraryApi:
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, path, method="GET", body=None, binary=False):
        headers = {"X-IOT-API-Key": self.api_key}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                result = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Library API returned {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Cannot connect to the Smart Library API: {error.reason}") from error
        except OSError as error:  # read timeouts / dropped connections (e.g. while Render restarts)
            raise RuntimeError(f"Cannot reach the Smart Library API: {error}") from error
        if binary:
            return result
        try:
            return json.loads(result.decode("utf-8"))
        except ValueError as error:
            raise RuntimeError("The Smart Library API returned an unexpected response.") from error

    def register_pending_faces(self, recognizer):
        pending = self._request("/api/iot/face-enrollments")["people"]
        for person in pending:
            image_bytes = self._request(
                f"/api/iot/people/{person['id']}/face-image", binary=True
            )
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                print(f"Skipped invalid face image for {person['school_id']}.")
                continue
            try:
                encoding = recognizer.encode(image)
            except ValueError as error:
                print(f"Face registration skipped for {person['school_id']}: {error}")
                continue
            self._request(
                f"/api/iot/people/{person['id']}/face-encoding",
                method="PUT",
                body={"encoding": encoding},
            )
            print(f"Face registered for {person['name']} ({person['school_id']}).")

    def get_face_templates(self):
        return self._request("/api/iot/face-templates")["people"]

    def record_attendance(self, person, action=None):
        event = action or ("time-out" if person["is_inside"] else "time-in")
        if event not in ("time-in", "time-out"):
            raise ValueError("Attendance action must be time-in or time-out.")
        return self._request(
            f"/api/attendance/{event}",
            method="POST",
            body={"school_id": person["school_id"]},
        )