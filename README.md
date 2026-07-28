# WeldFlex Software


## Run Flask App
python backend\app.py

## Install RPI Kiosk
sudo apt update && sudo apt install -y git

git clone https://github.com/gagesmith3/WeldFlex.git ~/WeldFlex
cd ~/WeldFlex
cp deploy/rpi/.env.rpi.example .env          # installer only warns, won't do this

sudo nmcli con add type ethernet ifname eth0 con-name robot-net \
    ipv4.method manual ipv4.addresses 192.168.58.100/24
sudo nmcli con up robot-net

sudo bash deploy/rpi/install_rpi_kiosk.sh
sudo reboot

## Redeploys
cd ~/WeldFlex && git pull
sudo systemctl restart weldflex-backend
pkill cage                             # supervising loop relaunches it — no reboot

