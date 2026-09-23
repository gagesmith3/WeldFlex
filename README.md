# WeldFlex Software

Host-side control software for a **FAIRINO FR-16 cobot doing stud welding**.
WeldFlex doesn't do motion control — Fairino's controller does that. WeldFlex
owns everything around it: the part library, generating the Lua program from a
part, pushing it to the controller over the vendor SDK, and running the job
(state, progress, controls, history) from a kiosk touchscreen so the operator
never has to touch the teach pendant.

**Normal operation:** pick a part → enter a cycle count → the job loads into the
Job Manager → hit Run → it runs the requested cycles → it completes.

> **A live run welds for real.** The generated program searches, presses, fires
> the weld output after its interlock checks, retracts, advances the feeder, and
> returns home between cycles. A dry run follows the same sequence but suppresses
> the weld output. Per-stud operator waits are not implemented. See
> [Not yet implemented](docs/ARCHITECTURE.md#not-yet-implemented).

> **Pendant preflight:** Set the FAIRINO pendant's **Auto Speed** to **100%**
> before running a part. It globally limits the program's requested speed,
> including Dynamic Speed Compensation (DSC); a lower pendant setting prevents
> a generated 100% stud-to-stud move from reaching its intended speed.

Full picture — the four layers, how a part becomes a program,
 job states, and the
current gaps: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
Intent spec: [orderofevent.md](orderofevent.md).
Robot connection ownership, telemetry sources, freshness, and recovery:
[docs/ROBOT_TELEMETRY.md](docs/ROBOT_TELEMETRY.md).

## Run Flask App

```
cd venv/Scripts && activate.bat
cd ..
cd .. 
python backend\app.py
```

## Run modes

Every run is **Live** or **Dry**, picked in the run modal each time; there is no
default. Dry runs the full search, press, retract and feed sequence without
pulsing the weld output. Each part also saves a **DI check** setting, on by
default. With it off, the DI0 welder-ready and DI1 stud-on-work checks are
skipped, on live runs too, and the job panel shows **DI OFF**.

Admin's **Single Shot** tool welds one stud at a saved target point, with the
same Live/Dry choice, then feeds the next stud and stays over the target.

## Install RPI Kiosk

```
sudo apt update && sudo apt install -y git

git clone https://github.com/gagesmith3/WeldFlex.git ~/WeldFlex
cd ~/WeldFlex
cp deploy/rpi/.env.rpi.example .env          # installer only warns, won't do this

sudo nmcli con add type ethernet ifname eth0 con-name robot-net \
    ipv4.method manual ipv4.addresses 192.168.58.100/24 \
    ipv4.never-default yes ipv6.method disabled   # else eth0 steals the default route
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

A pull that changes what the installer copies into the system (the nginx
proxy conf, the touch rule, the service unit) only takes effect after re-running
`sudo bash deploy/rpi/install_rpi_kiosk.sh`.
