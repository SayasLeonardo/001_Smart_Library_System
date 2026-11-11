# Raspberry Pi face-recognition station

The Pi uses its Camera Module to recognize registered people and sends Time-in / Time-out to your online
Smart Library System. The screen on the Pi is a full-screen browser showing the station page
(`http://127.0.0.1:5001`, served by the Pi itself). The Pi only makes **outgoing** HTTPS connections, so
it works on any network (school Wi-Fi, mobile hotspot) without port forwarding, and the librarian can
watch attendance from any device.

## What you need

- Raspberry Pi 4 or 5 (a Pi 3 can work but will be slow), power supply, microSD card, screen.
- Raspberry Pi Camera Module connected with the ribbon cable (power off while connecting).
- **Raspberry Pi OS 64-bit with desktop** (Bookworm or newer), connected to the internet.
- Your server address (`https://library...`) and the IoT key from the server
  (`sudo grep SMART_LIBRARY_IOT_KEY /etc/smart-library/smart-library.env` on the VPS).

## Setup (about 10 minutes)

1. Flash Raspberry Pi OS (64-bit, with desktop) using Raspberry Pi Imager. In its settings, set the
   username/password, Wi-Fi and enable SSH.
2. Boot the Pi, then update and download the project:
   ```bash
   sudo apt-get update && sudo apt-get full-upgrade -y
   sudo apt-get install -y git
   git clone https://github.com/SayasLeonardo/001_Smart_Library_System.git ~/smart-library
   ```
   (Private repository? Use a GitHub access token in the URL, or copy the folder with a USB drive.)
3. Run the setup script (as your normal user, **not** with sudo):
   ```bash
   bash ~/smart-library/face_recognition_iot/raspberry_pi/setup_pi.sh https://library.yourschool.edu.ph
   ```
   It asks for the IoT key. It installs the camera and face libraries, downloads the face models,
   saves the connection settings (root-only file `/etc/smart-library-station.env`), tests the connection,
   starts the station at every boot, and makes the Pi sign in to the desktop and open the station
   full-screen automatically.
4. Reboot: `sudo reboot`. After about a minute the station screen appears by itself.

## How the browser opens automatically

- `smart-library-station.service` (systemd) starts the face-recognition program at boot and restarts it if it stops.
- The setup script adds one line to `~/.config/labwc/autostart` that runs `kiosk.sh`. `kiosk.sh` waits until the
  station answers, then opens Chromium full-screen (`--kiosk`) at `http://127.0.0.1:5001/`.
- Desktop auto-login is enabled with `raspi-config` (System Options > Boot / Auto Login > Desktop Autologin),
  and screen blanking is turned off.
- Older Raspberry Pi OS (Bullseye, X11) does not use labwc: add `@~/smart-library/face_recognition_iot/raspberry_pi/kiosk.sh`
  to a copy of `/etc/xdg/lxsession/LXDE-pi/autostart` in `~/.config/lxsession/LXDE-pi/autostart`
  (copy the system file first so the desktop panel keeps working).

## Using the station

Default (confirm mode, the original behaviour): the station recognizes the person, you choose **Time-in** or
**Time-out** on screen, check the name and press confirm. Recognition alone never saves attendance.

Automatic mode: edit `/etc/smart-library-station.env`, set `SMART_LIBRARY_AUTO_ATTENDANCE=1`, then
`sudo systemctl restart smart-library-station`. A registered, active person who steps in front of the camera is
saved immediately: first visit = Time-in, next visit = Time-out. Safeguards: the server rejects duplicates, a person
must leave the camera view before they can be recorded again, and `SMART_LIBRARY_AUTO_COOLDOWN` (default 60 s)
sets the minimum time between records for the same person. Consider whether automatic recording suits your
library (someone walking past the camera will be recorded).

New faces: when the MIS Admin uploads a photo, the server registers it, or the Pi does within about 30 seconds.
The Pi refreshes the list of registered faces every 30 seconds.

## Everyday commands (over SSH or a terminal)

| Task | Command |
|---|---|
| Station status | `systemctl status smart-library-station` |
| Live log | `journalctl -u smart-library-station -f` |
| Restart station | `sudo systemctl restart smart-library-station` |
| Change server address / key / mode | `sudo nano /etc/smart-library-station.env` then restart the station |
| Leave full-screen to maintain the Pi | connect by SSH and run `pkill chromium` (it is reopened at next login) |
| Test the camera on its own | `rpicam-hello` (older systems: `libcamera-hello`) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Station says "camera is not available" or loops on camera errors | Check the ribbon cable and orientation, power off and on, run `rpicam-hello --list-cameras`. Another program using the camera also blocks it. |
| "Cannot connect to the Smart Library API" | Check internet on the Pi and the address in `/etc/smart-library-station.env`. Open the address in a browser. Pi clock must be roughly right (it syncs automatically when online). |
| "Library API returned 401" | The key on the Pi differs from the server's. Copy it again from the VPS and restart the station. |
| `numpy` / `simplejpeg` import errors after installing | Re-run `~/smart-library/face_recognition_iot/.venv/bin/pip install "numpy<2"` and restart the station. |
| Model download fails | Needs internet on first start. Restart the station when connected. |
| Browser does not open at boot | `cat ~/.config/labwc/autostart` should contain the `kiosk.sh` line; check desktop auto-login (`sudo raspi-config`). Run `kiosk.sh` by hand to see errors. |
| Person not recognized | Better lighting, face the camera, re-upload a clearer front-facing photo. |
| Sends two records / wrong action | Confirm mode: check the selected Time-in/Time-out. Auto mode: lengthen `SMART_LIBRARY_AUTO_COOLDOWN`. |

## Security and privacy

- The station page has no login of its own, so it listens on `127.0.0.1` only. Do not change `SMART_LIBRARY_STATION_HOST` to `0.0.0.0`.
- The IoT key lives in a root-only file. Do not paste it into chats or commit it to GitHub.
- Camera frames and face data stay in memory on the Pi; photos and face encodings are stored on your server.
  Face data is biometric personal data: get consent and follow your school's data privacy policy
  (Philippines Data Privacy Act).

## Not tested on real hardware

The server side, API, recognition-to-attendance flow and automatic mode were tested with automated checks.
The Raspberry Pi installation scripts and the kiosk start-up could not be run on a real Pi in this environment, so
do the first install with the Pi on your desk, watch the script output, and use the troubleshooting table above.
