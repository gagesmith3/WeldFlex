# WeldFlex Backend Scaffold

This project now includes a Flask backend scaffold for controlling a FAIRINO FR-16 robot program from an operator UI.

## Architecture Flow

Core building blocks:

- UI layer: Flask + Jinja templates + HTMX partial updates + CSS/JS assets
- API/UI routes: `backend/app.py`
- Robot integration service: `backend/robot_service.py`
- Lua payload builder: `backend/lua_builder.py`
- Local recipe persistence: `data/recipes.json`

End-to-end run flow:

1. Operator edits X/Y rows in Part Designer.
2. UI posts to `/ui/run`.
3. `app.py` validates/parses studs and calls `WeldFlexRobotService.upload_load_run(...)`.
4. `robot_service.py` builds `studs_data.lua` via `lua_builder.py`.
5. Service uploads `/fruser/studs_data.lua` to the controller.
6. Service loads `/fruser/studCycle.lua`, sets auto mode, runs program.
7. UI polls `/ui/status` and `/ui/connection` for live state chips/status cards.

Page routing model:

- `/`: Home dashboard
- `/part-library`: blank scaffold page
- `/part-designer`: coordinate table + recipe workflow
- `/robot-diagnostics`: blank scaffold page
- `/settings`: blank scaffold page

## Template Macro System

Reusable UI macros are defined in `backend/templates/components/ui.html` and imported with context from page templates and partials.

Current macro catalog:

- `icon_link(href, icon, label, class_name, title)`: shared icon/link pattern used in the navbar home button.
- `action_button(...)`: unified button/link API for regular buttons, HTMX actions, and page links.
- `status_chip(...)`: compact and full-size status chips used in connection/status displays.
- `metric_card(...)`: summary metric card used on the home hero panel.
- `nav_card(...)`: home navigation cards for route shortcuts.
- `panel_shell(title, subtitle, class_name)`: consistent panel wrapper (heading/subheading/body via caller block).
- `htmx_mount(id, endpoint, trigger, swap, class_name)`: generic HTMX polling/loading container.
- `live_status_mount(...)`: convenience wrapper over `htmx_mount` for `/ui/status` polling.
- `result_mount(id, class_name)`: empty result container mount points.
- `command_result_card(ok, title, payload)`: standard command response renderer.
- `robot_status_card(ok, status)`: standard robot status renderer.
- `labeled_block(label, for_id, class_name)`: repeated form block wrapper for labeled control groups.

Where macros are currently applied:

- `backend/templates/base.html`: navbar home icon, E-STOP button, connection chips mount, global result mount.
- `backend/templates/home.html`: hero actions, metric cards, nav cards, panel shells.
- `backend/templates/operator.html`: drawer/workspace panel shells, recipe/status mounts, control actions.
- `backend/templates/partials/command_result.html`: thin wrapper over `command_result_card`.
- `backend/templates/partials/status.html`: thin wrapper over `robot_status_card`.
- `backend/templates/partials/recipe_library.html`: `action_button` + `labeled_block` composition.

Guideline for new UI work:

1. Prefer extending existing macros before adding one-off template markup.
2. Keep HTMX attributes centralized in macro calls where practical.
3. Keep partials thin (data mapping only) and move repeated markup into `components/ui.html`.

## Implemented API Endpoints

- `POST /api/run`
- `POST /api/pause`
- `POST /api/stop`
- `GET /api/status`
- `GET /api/health`

## Request/Response Basics

### Run Program

`POST /api/run`

Body:

```json
{
  "program_path": "/fruser/studCycle.lua",
  "studs": [
    { "x": 10.0, "y": 5.5 },
    { "x": 25.0, "y": 5.5 }
  ]
}
```

Behavior:

1. Generates `studs_data.lua` from `studs`.
2. Uploads `studs_data.lua` to controller.
3. Loads the fixed base program (`studCycle.lua`).
3. Calls:
   - `ProgramLoad(program_path)`
   - `Mode(0)`
   - `ProgramRun()`

### Pause

`POST /api/pause`

### Stop

`POST /api/stop`

### Status

`GET /api/status`

Returns `program_state_raw`, mapped `program_state`, and `current_line`.

## Environment Variables

- `WELDFLEX_ROBOT_IP` (default: `192.168.58.2`)
- `WELDFLEX_CONTROLLER_HOST` (default: same as robot IP)
- `WELDFLEX_PROGRAM_PATH` (default: `/fruser/studCycle.lua`)
- `WELDFLEX_STUDS_DATA_PATH` (default: `/fruser/studs_data.lua`)
- `WELDFLEX_FAIRINO_PATH` (optional explicit SDK path, e.g. `C:/.../WeldFlex/fairino-python-sdk-main/windows`)
- `WELDFLEX_FTP_USER` (default: `anonymous`)
- `WELDFLEX_FTP_PASS` (default: empty)
- `PORT` (default: `5000`)

Important for base program behavior:

`/fruser/studCycle.lua` should load the generated studs file before iterating studs, for example:

```lua
NewDofile("/fruser/studs_data.lua",1,1)
DofileEnd()
```

and `studs_data.lua` should define:

```lua
studs = {
  {x=151, y=151},
  {x=200, y=151}
}
```

Recommended: create a `.env` file in the workspace root and keep all variables there.
Use `.env.example` as your template.

By default, the backend auto-detects local SDK folders at:

- `./fairino-python-sdk-main/windows`
- `./fairino-python-sdk-main/linux`

## Upload Strategy

The scaffold attempts SDK upload first using one of these methods if exposed by your FAIRINO Python SDK object:

- `FileUpload(local, remote)`
- `UploadFile(local, remote)`
- `ProgramUpload(local, remote)`
- `UploadProgram(local, remote)`

If none are available, it falls back to FTP upload.

## Run Locally

```bash
pip install -r requirements.txt
copy .env.example .env
python -m backend.app
```

If auto-detection does not find your SDK location, set:

```bash
set WELDFLEX_FAIRINO_PATH=C:/Users/Gage/Desktop/WeldFlex/fairino-python-sdk-main/windows
python -m backend.app
```

If you use `.env`, you do not need to run `set` every time.

Then test:

```bash
curl http://127.0.0.1:5000/api/health
```
c:\Users\Gage\Desktop\WeldFlex\venv\Scripts\activate.bat
python -m backend.app

## Raspberry Pi 4 Kiosk Deployment

Use this path to boot a Pi directly into WeldFlex fullscreen on startup.

### One-Command Installer (Recommended)

From the repo root on the Pi:

```bash
chmod +x deploy/rpi/install_rpi_kiosk.sh
./deploy/rpi/install_rpi_kiosk.sh
```

What it does:

- Installs required OS packages (`python3-venv`, Chromium, `unclutter`, `git`)
- Creates `venv` and installs `requirements.txt`
- Creates `.env` from `.env.example` if missing
- Sets `WELDFLEX_FAIRINO_PATH` to the repo Linux SDK path
- Runs `scripts/rpi_preflight.py`
- Installs and enables `weldflex-update.service`, `weldflex-backend.service`, and `weldflex-kiosk.service`

Boot-time update behavior:

- On each boot, `weldflex-update.service` runs first.
- It fetches `origin/main` and fast-forwards local code only when safe.
- It skips auto-update if local changes exist or branch state is diverged/ahead.
- After successful update, it refreshes Python dependencies from `requirements.txt`.
- Then backend and kiosk services start using the updated code.

If your kiosk login user is not `pi`, run with:

```bash
WELDFLEX_TARGET_USER=<your_user> ./deploy/rpi/install_rpi_kiosk.sh
```

After install, edit `.env` to confirm robot/controller IP values for your network, then reboot.

### 1) Prepare Pi OS

- Raspberry Pi OS Bookworm with Desktop (recommended for kiosk mode).
- Install required packages:

```bash
sudo apt update
sudo apt install -y python3-venv chromium unclutter
```

If `chromium` is unavailable on your image, try:

```bash
sudo apt install -y python3-venv chromium-browser unclutter
```

### 2) Copy project to Pi

Put the repo at `/home/pi/WeldFlex`.

### 3) Create venv and install Python dependencies

```bash
cd /home/pi/WeldFlex
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4) Configure runtime environment

```bash
cp .env.example .env
```

Set values in `.env` for your robot/controller network. For Linux/Pi, set:

```bash
WELDFLEX_FAIRINO_PATH=/home/pi/WeldFlex/fairino-python-sdk-main/linux
```

### 5) Run preflight checks

This validates FAIRINO import and app health bootstrap.

```bash
cd /home/pi/WeldFlex
source venv/bin/activate
python scripts/rpi_preflight.py
```

### 6) Install backend systemd service

Service template in repo: `deploy/rpi/weldflex-backend.service`

```bash
sudo cp /home/pi/WeldFlex/deploy/rpi/weldflex-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable weldflex-backend.service
sudo systemctl start weldflex-backend.service
sudo systemctl status weldflex-backend.service
```

### 6.5) Install boot update-check service

Service template in repo: `deploy/rpi/weldflex-update.service`

```bash
chmod +x /home/pi/WeldFlex/deploy/rpi/update_on_boot.sh
sudo cp /home/pi/WeldFlex/deploy/rpi/weldflex-update.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable weldflex-update.service
sudo systemctl start weldflex-update.service
sudo systemctl status weldflex-update.service
```

### 7) Install kiosk browser service

Make launcher executable and install service:

```bash
chmod +x /home/pi/WeldFlex/deploy/rpi/kiosk-launch.sh
sudo cp /home/pi/WeldFlex/deploy/rpi/weldflex-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable weldflex-kiosk.service
sudo systemctl start weldflex-kiosk.service
sudo systemctl status weldflex-kiosk.service
```

Kiosk URL defaults to `http://127.0.0.1:5000` and can be changed in `deploy/rpi/weldflex-kiosk.service` via `WELDFLEX_KIOSK_URL`.
Browser command can be overridden in `deploy/rpi/weldflex-kiosk.service` via `WELDFLEX_BROWSER_CMD` (for example `chromium` on newer Pi OS).
Update source defaults to `origin/main` and can be changed by editing `deploy/rpi/update_on_boot.sh`.

### 8) Verify end-to-end

- Reboot Pi.
- Confirm Flask backend is up:

```bash
curl http://127.0.0.1:5000/api/health
```

- Display should auto-open Chromium in fullscreen kiosk mode on WeldFlex.

### Notes

- If FAIRINO Linux SDK is not ARM-compatible, imports can fail on Pi even if app code is correct.
- Keep a physical independent E-stop and machine safety chain; do not rely on software UI stop for personnel safety.