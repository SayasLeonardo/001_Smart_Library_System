"use strict";

const cameraStatus = document.getElementById("cameraStatus");
const cameraDot = document.getElementById("cameraDot");
const cameraFeed = document.getElementById("cameraFeed");
const cameraPlaceholder = document.getElementById("cameraPlaceholder");
const faceStatus = document.getElementById("faceStatus");
const stationMessage = document.getElementById("stationMessage");
const timeInButton = document.getElementById("timeInButton");
const timeOutButton = document.getElementById("timeOutButton");
const recognitionCard = document.getElementById("recognitionCard");
const resultEyebrow = document.getElementById("resultEyebrow");
const resultTitle = document.getElementById("resultTitle");
const resultMark = document.getElementById("resultMark");
const personDetails = document.getElementById("personDetails");
const personSchoolId = document.getElementById("personSchoolId");
const personName = document.getElementById("personName");
const personRole = document.getElementById("personRole");
const personDepartment = document.getElementById("personDepartment");
const recordedTime = document.getElementById("recordedTime");
const confirmationActions = document.getElementById("confirmationActions");
const confirmButton = document.getElementById("confirmButton");
const cancelButton = document.getElementById("cancelButton");

let feedRetryTimer = null;

function formatManilaTimestamp(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return new Intl.DateTimeFormat("en-PH", {
    timeZone: "Asia/Manila",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true
  }).format(timestamp);
}

async function request(path, options) {
  const response = await fetch(path, Object.assign({
    headers: { "Content-Type": "application/json" }
  }, options || {}));
  const result = await response.json().catch(function () { return {}; });
  if (!response.ok) throw new Error(result.error || "The station request failed.");
  return result;
}

function setCameraStatus(status, message) {
  cameraStatus.textContent = message;
  cameraDot.classList.toggle("ready", status === "active");
  cameraDot.classList.toggle("error", status === "error" || status === "camera_error" || status === "backend_error");
  cameraPlaceholder.style.display = status === "active" ? "none" : "grid";
  if (status !== "active") cameraPlaceholder.textContent = message;
}

function fillPerson(person) {
  personSchoolId.textContent = person.school_id || "-";
  personName.textContent = person.name || "-";
  personRole.textContent = person.role || "-";
  personDepartment.textContent = person.department || "-";
}

function showErrorCard(title, message) {
  recognitionCard.hidden = false;
  recognitionCard.className = "recognition-card error";
  resultEyebrow.textContent = "ATTENDANCE NOT RECORDED";
  resultTitle.textContent = title;
  resultMark.textContent = "!";
  personDetails.hidden = true;
  recordedTime.hidden = true;
  recordedTime.textContent = "";
  confirmationActions.hidden = true;
  stationMessage.textContent = message;
  stationMessage.className = "station-message error";
}

function renderStation(state) {
  setCameraStatus(state.camera_status, state.camera_status === "active" ? "Camera active" : state.message);

  const action = state.selected_action;
  timeInButton.setAttribute("aria-pressed", String(action === "time_in"));
  timeOutButton.setAttribute("aria-pressed", String(action === "time_out"));

  faceStatus.textContent = {
    ready: "Waiting for a face",
    recognized: "Face recognized",
    unknown: "Face not recognized",
    multiple_faces: "One person at a time",
    recorded: "Attendance recorded",
    error: "Attendance error"
  }[state.face_status] || "Waiting for a face";
  faceStatus.className = "face-status";
  if (state.face_status === "recognized" || state.face_status === "recorded") faceStatus.classList.add("detected");
  if (state.face_status === "unknown" || state.face_status === "multiple_faces" || state.face_status === "error") {
    faceStatus.classList.add("unknown");
  }

  if (state.result) {
    const isTimeIn = state.result.action === "time_in";
    recognitionCard.hidden = false;
    recognitionCard.className = "recognition-card success";
    resultEyebrow.textContent = isTimeIn ? "TIME-IN SUCCESS" : "TIME-OUT SUCCESS";
    resultTitle.textContent = "Attendance recorded";
    resultMark.textContent = "✓";
    personDetails.hidden = false;
    fillPerson(state.result.person);
    recordedTime.hidden = false;
    recordedTime.textContent = formatManilaTimestamp(state.result.timestamp);
    confirmationActions.hidden = true;
    stationMessage.textContent = state.message;
    stationMessage.className = "station-message success";
    return;
  }

  if (state.face_status === "unknown") {
    showErrorCard("Face not recognized", state.message || "No registered person was found. Please contact the library administrator.");
    return;
  }

  if (state.face_status === "multiple_faces") {
    showErrorCard("One person at a time", state.message);
    return;
  }

  if (state.face_status === "error" || state.camera_status === "error" || state.camera_status === "backend_error") {
    showErrorCard("Station unavailable", state.message);
    return;
  }

  if (state.pending_person) {
    recognitionCard.hidden = false;
    recognitionCard.className = "recognition-card";
    resultEyebrow.textContent = "FACE RECOGNIZED";
    resultTitle.textContent = "Confirm attendance";
    resultMark.textContent = "?";
    personDetails.hidden = false;
    fillPerson(state.pending_person);
    recordedTime.hidden = true;
    confirmationActions.hidden = false;
    confirmButton.disabled = !state.can_confirm;
    confirmButton.textContent = action === "time_in" ? "Confirm Time-in" : (action === "time_out" ? "Confirm Time-out" : "Select an action first");
    stationMessage.textContent = action ? state.action_message : "Choose Time-in or Time-out, then confirm this person.";
    stationMessage.className = state.action_message && (
      state.action_message.startsWith("Already Timed-in") ||
      state.action_message.startsWith("Time-out cannot") ||
      state.action_message.startsWith("Inactive person")
    ) ? "station-message error" : "station-message";
    return;
  }

  recognitionCard.hidden = true;
  stationMessage.textContent = state.message || "Select Time-in or Time-out before confirming attendance.";
  stationMessage.className = "station-message";
}

async function refreshStation() {
  try {
    renderStation(await request("/api/state"));
  } catch (error) {
    setCameraStatus("error", error.message);
    stationMessage.textContent = error.message;
    stationMessage.className = "station-message error";
  }
}

async function selectAction(action) {
  try {
    renderStation(await request("/api/action", {
      method: "POST",
      body: JSON.stringify({ action: action })
    }));
  } catch (error) {
    stationMessage.textContent = error.message;
    stationMessage.className = "station-message error";
  }
}

timeInButton.addEventListener("click", function () { selectAction("time_in"); });
timeOutButton.addEventListener("click", function () { selectAction("time_out"); });

confirmButton.addEventListener("click", async function () {
  confirmButton.disabled = true;
  try {
    renderStation(await request("/api/confirm", { method: "POST", body: "{}" }));
  } catch (error) {
    stationMessage.textContent = error.message;
    stationMessage.className = "station-message error";
    await refreshStation();
  }
});

cancelButton.addEventListener("click", async function () {
  try {
    renderStation(await request("/api/cancel", { method: "POST", body: "{}" }));
  } catch (error) {
    stationMessage.textContent = error.message;
    stationMessage.className = "station-message error";
  }
});

// Live video: the <img> points at the MJPEG stream (/video_feed).
// If the stream drops (server restart, network blip), reconnect automatically.
function connectFeed() {
  cameraFeed.src = "/video_feed?t=" + Date.now();
}

cameraFeed.addEventListener("load", function () {
  cameraPlaceholder.style.display = "none";
});
cameraFeed.addEventListener("error", function () {
  cameraPlaceholder.style.display = "grid";
  cameraPlaceholder.textContent = "Camera feed unavailable. Reconnecting...";
  if (feedRetryTimer === null) {
    feedRetryTimer = window.setTimeout(function () {
      feedRetryTimer = null;
      connectFeed();
    }, 2000);
  }
});

connectFeed();
refreshStation();
window.setInterval(refreshStation, 500);
