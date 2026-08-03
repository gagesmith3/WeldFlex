# WeldFlex Software

Host-side control software for a **FAIRINO FR-16 cobot doing stud welding**.
WeldFlex doesn't do motion control — Fairino's controller does that. WeldFlex
owns everything around it: the part library, generating the Lua program from a
part, pushing it to the controller over the vendor SDK, and running the job
(state, progress, controls, history) from a kiosk touchscreen so the operator
never has to touch the teach pendant.

**Normal operation:** pick a part → enter a cycle count → the job loads into the
Job Manager → hit Run → it runs the requested cycles → it completes.

> ⚠️ **WeldFlex does not weld yet.** The generated program moves the head to each
> stud and dwells; the weld call, the return-to-home, and per-stud operator waits
> are all still to be built. See
> [Not yet implemented](docs/ARCHITECTURE.md#not-yet-implemented).

Full picture — the four layers, how a part becomes a program,
 job states, and the
current gaps: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
Intent spec: [orderofevent.md](orderofevent.md).
Robot connection ownership, telemetry sources, freshness, and recovery:
[docs/ROBOT_TELEMETRY.md](docs/ROBOT_TELEMETRY.md).

## Run Flask App

```
cd venv/Scripts && activate.bat
cd 
python backend\app.py
```

## Install RPI Kiosk

```
sudo apt update && sudo apt install -y git

git clone https://github.com/gagesmith3/WeldFlex.git ~/WeldFlex
cd ~/WeldFlex
cp deploy/rpi/.env.rpi.example .env          # installer only warns, won't do this

sudo nmcli con add type ethernet ifname eth0 con-name robot-net \
    ipv4.method manual ipv4.addresses 192.168.58.100/24
sudo nmcli con up robot-net

sudo bash deploy/rpi/install_rpi_kiosk.sh
sudo reboot
```

## Redeploys

```
cd ~/WeldFlex && git pull
sudo systemctl restart weldflex-backend
pkill cage   # supervising loop relaunches it — no reboot
```
