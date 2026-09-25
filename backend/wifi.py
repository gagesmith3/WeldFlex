"""The kiosk's Wi-Fi, driven through NetworkManager's `nmcli`.

This module is host-OS plumbing and sits outside the robot chain
(`job_manager` → `robot_service` → `robot_link`). It never talks to the robot.

**The rule this module exists to keep: the robot link is never touched.** The
HMI reaches the controller over `eth0` on a static profile (`robot-eth0`,
`ipv4.never-default yes`); Wi-Fi is only for the shop network and internet. So:

- Every command names the Wi-Fi interface (`ifname wlan0`). Nothing here brings
  a device down, and nothing edits or deletes a profile unless its type is
  `802-11-wireless`. `forget()` checks the type of each profile it deletes.
- Before a connect, the interface that routes to the robot and its subnet are
  snapshotted. After the connect, the route is checked again. If Wi-Fi landed on
  the robot's subnet, or the robot's route moved off its interface, the new
  network is rolled back.
- A failed or rolled-back connect deletes the profiles it created and brings
  back the Wi-Fi profile that was active before. The same approach as
  idulkoan/rpi-wifi-config.

Connecting takes up to `CONNECT_TIMEOUT_S` seconds, so `start_connect()` runs it
on a thread and the card polls `operation()`. Only one operation runs at a time.

**Hotspot.** For a trade show with no Wi-Fi to join, the panel can broadcast its
own network instead (`start_hotspot()`), so a laptop or phone can reach the app
and SSH at `HOTSPOT_ADDR`. The radio cannot be a client and an access point at
once, so the two are either/or: starting the hotspot leaves the shop Wi-Fi, and
joining a network (`start_connect()`) turns the hotspot off first. If that join
fails, the hotspot comes back on. While it is on, its profile (`HOTSPOT_NAME`)
autoconnects at a high priority, so it comes back after an overnight power-off.
`stop_hotspot()` and a join both turn that off. An access point cannot scan, so
while the hotspot is on `status()` lists the saved networks instead. NetworkManager's
"shared" mode hands out addresses and turns on IP forwarding, which would let a
hotspot device route through the panel to the robot. The installer's dispatcher
script (`deploy/rpi/90-weldflex-wifi-noforward`) turns forwarding off for
packets that arrive on Wi-Fi, and the card warns when it is not in place.

Privilege: the backend runs as the kiosk user, not root. The installer's polkit
rule (`deploy/rpi/10-weldflex-wifi.rules`) grants that user the NetworkManager
actions this module needs. Without it every change fails with "Not authorized".

The Wi-Fi password is passed to `nmcli` on its command line, because
`device wifi connect` takes it there. While that command runs, another local
user could read it with `ps`. On a single-user kiosk this is acceptable. The
password is never logged, and NetworkManager stores it root-only.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

IFACE = os.getenv("WELDFLEX_WIFI_IFACE", "wlan0")
CONNECT_TIMEOUT_S = 30
WIFI_TYPE = "802-11-wireless"

HOTSPOT_NAME = "weldflex-hotspot"
HOTSPOT_ADDR = ipaddress.IPv4Interface("10.42.0.1/24")
# Written to the hotspot profile on every start. It uses 2.4 GHz because the most
# devices support it. It is WPA2-only (CCMP) with PMF off, because in AP mode the
# Pi's brcmfmac driver is known to reject clients under WPA3/SAE or PMF.
HOTSPOT_SETTINGS = [
    "connection.autoconnect", "yes",
    "connection.autoconnect-priority", "100",
    "802-11-wireless.mode", "ap",
    "802-11-wireless.band", "bg",
    "ipv4.method", "shared",
    "ipv4.addresses", str(HOTSPOT_ADDR),
    "ipv6.method", "disabled",
    "802-11-wireless-security.key-mgmt", "wpa-psk",
    "802-11-wireless-security.proto", "rsn",
    "802-11-wireless-security.pairwise", "ccmp",
    "802-11-wireless-security.group", "ccmp",
    "802-11-wireless-security.pmf", "disable",
]
# What the start sheet offers until a hotspot has been saved. The owner picked a
# fixed, memorable one so booth staff don't have to read it off the panel.
DEFAULT_HOTSPOT_PASSWORD = "iloveiwt"

# How long a finished connect/forget result stays on the card.
RESULT_SHOW_S = 90


class WifiError(Exception):
    """An nmcli/ip command failed. The message is safe to show the operator."""


@dataclass
class Network:
    ssid: str
    signal: int
    security: str          # nmcli's SECURITY column, "" for an open network
    in_use: bool = False
    saved: bool = False
    seen: bool = True      # False: a saved profile listed while the hotspot is on, not scanned

    @property
    def secured(self) -> bool:
        return bool(self.security)

    @property
    def enterprise(self) -> bool:
        return "802.1X" in self.security

    @property
    def bars(self) -> int:
        return 0 if self.signal < 20 else 1 if self.signal < 45 else 2 if self.signal < 70 else 3


@dataclass
class Operation:
    kind: str = ""               # "connect" | "forget" | "hotspot" | "hotspot-stop" | ""
    ssid: str = ""
    state: str = "idle"          # "idle" | "running" | "ok" | "failed"
    message: str = ""
    finished_at: float = 0.0

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def visible(self) -> bool:
        if self.running:
            return True
        return self.state in ("ok", "failed") and time.time() - self.finished_at < RESULT_SHOW_S


@dataclass
class Hotspot:
    saved: bool = False          # a hotspot profile exists
    on: bool = False             # and it is the active Wi-Fi connection
    ssid: str = ""               # the saved name, or a suggested default
    password: str = ""           # the saved password, or the default while none is saved
    address: str = ""
    isolated: bool = True        # hotspot devices cannot route through to the robot network


@dataclass
class WifiStatus:
    available: bool
    reason: str = ""
    radio_on: bool = False
    ssid: str = ""
    signal: int | None = None
    address: str = ""
    robot_dev: str = ""
    robot_ok: bool = False
    networks: list[Network] = field(default_factory=list)
    hotspot: Hotspot = field(default_factory=Hotspot)
    error: str = ""


_op = Operation()
_op_lock = threading.Lock()


# ── command helpers ───────────────────────────────────────────────────────────

def available() -> tuple[bool, str]:
    if sys.platform == "win32":
        return False, "Wi-Fi can only be changed on the kiosk. This machine manages its own."
    if not shutil.which("nmcli"):
        return False, "NetworkManager (nmcli) is not installed on this machine."
    return True, ""


def _run(args: list[str], timeout: float = 15) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise WifiError(f"{args[0]} timed out after {timeout:.0f} s") from None
    except OSError as exc:
        raise WifiError(str(exc)) from None
    if proc.returncode != 0:
        raise WifiError(_friendly(proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"))
    return proc.stdout


def _nmcli(*args: str, timeout: float = 15) -> str:
    return _run(["nmcli", *args], timeout=timeout)


def _friendly(msg: str) -> str:
    """Turn nmcli's messages into something an operator can act on."""
    low = msg.lower()
    if "secrets were required" in low or ("psk" in low and "invalid" in low):
        return "Wrong password."
    if "no network with ssid" in low or "could not be found" in low:
        return "Network not found. Move closer or scan again."
    if "not authorized" in low or "insufficient privileges" in low:
        return "Not permitted. Re-run the kiosk installer to add the Wi-Fi permission rule."
    if msg.startswith("Error: "):
        msg = msg[len("Error: "):]
    return msg.splitlines()[0] if msg else "Unknown error."


def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t` output. In that output `:` separates fields,
    and a `:` or `\\` inside a value is escaped with a backslash."""
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def _get(prop: str, uuid: str, secret: bool = False) -> str:
    """One property of a saved profile. `-g` output escapes `:` and `\\` as well."""
    out = _nmcli(*(["-s"] if secret else []), "-g", prop, "connection", "show", "uuid", uuid)
    return split_terse(out.rstrip("\n"))[0]


def _profiles() -> list[tuple[str, str, str]]:
    """(uuid, type, name) for every saved profile, of any type."""
    out = []
    for line in _nmcli("-t", "-f", "UUID,TYPE,NAME", "connection", "show").splitlines():
        parts = split_terse(line)
        if len(parts) >= 3:
            out.append((parts[0], parts[1], parts[2]))
    return out


def _wifi_profiles() -> list[tuple[str, str]]:
    """(uuid, ssid) for every saved Wi-Fi client profile. The hotspot's own profile
    and other profile types never appear."""
    return [(uuid, _get("802-11-wireless.ssid", uuid))
            for uuid, typ, name in _profiles() if typ == WIFI_TYPE and name != HOTSPOT_NAME]


def _hotspot_uuid() -> str:
    return next((u for u, t, n in _profiles() if t == WIFI_TYPE and n == HOTSPOT_NAME), "")


def _active_wifi_uuid() -> str:
    for line in _nmcli("-t", "-f", "UUID,TYPE,DEVICE", "connection", "show", "--active").splitlines():
        parts = split_terse(line)
        if len(parts) >= 3 and parts[1] == WIFI_TYPE and parts[2] == IFACE:
            return parts[0]
    return ""


def _iface_network(dev: str) -> ipaddress.IPv4Interface | None:
    try:
        data = json.loads(_run(["ip", "-j", "-4", "addr", "show", "dev", dev]) or "[]")
    except (WifiError, ValueError):
        return None
    for entry in data:
        for addr in entry.get("addr_info", []):
            if addr.get("family") == "inet":
                return ipaddress.IPv4Interface(f"{addr['local']}/{addr['prefixlen']}")
    return None


def _forwarding(dev: str) -> str:
    """The kernel's IPv4 forwarding flag for packets arriving on `dev`: "0", "1" or ""."""
    try:
        with open(f"/proc/sys/net/ipv4/conf/{dev}/forwarding", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _default_hotspot_ssid() -> str:
    try:
        with open(f"/sys/class/net/{IFACE}/address", encoding="ascii") as fh:
            mac = fh.read().strip()
    except OSError:
        return "WeldFlex"
    return "WeldFlex-" + mac.replace(":", "")[-4:].upper()


def _robot_route_dev(robot_ip: str) -> str:
    try:
        data = json.loads(_run(["ip", "-j", "route", "get", robot_ip]) or "[]")
    except (WifiError, ValueError):
        return ""
    return data[0].get("dev", "") if data else ""


# ── reads ─────────────────────────────────────────────────────────────────────

def status(robot_ip: str, rescan: str = "no") -> WifiStatus:
    """What the card shows. `rescan` is nmcli's `--rescan` value: "yes", "no" or "auto"."""
    ok, reason = available()
    if not ok:
        return WifiStatus(available=False, reason=reason)
    st = WifiStatus(available=True)
    try:
        st.radio_on = _nmcli("radio", "wifi").strip() == "enabled"
        st.robot_dev = _robot_route_dev(robot_ip)
        st.robot_ok = bool(st.robot_dev) and st.robot_dev != IFACE
        if not st.radio_on:
            return st
        st.hotspot = _hotspot_status()
        saved = {ssid for _, ssid in _wifi_profiles()}
        if st.hotspot.on:
            # An access point does not scan, so offer the saved networks to switch back to.
            st.ssid, st.address = st.hotspot.ssid, st.hotspot.address
            st.networks = [Network(ssid=s, signal=0, security="", saved=True, seen=False)
                           for s in sorted(saved, key=str.lower)]
            return st
        seen: dict[str, Network] = {}
        out = _nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                     "ifname", IFACE, "--rescan", rescan, timeout=25)
        for line in out.splitlines():
            parts = split_terse(line)
            if len(parts) < 4 or not parts[1]:
                continue    # hidden networks broadcast no SSID; "Other network" covers them
            sec = "" if parts[3] in ("", "--") else parts[3]
            net = Network(ssid=parts[1], signal=int(parts[2] or 0), security=sec,
                          in_use=parts[0].strip() == "*", saved=parts[1] in saved)
            prev = seen.get(net.ssid)
            if prev is None or net.in_use or (not prev.in_use and net.signal > prev.signal):
                seen[net.ssid] = net
        st.networks = sorted(seen.values(), key=lambda n: (not n.in_use, not n.saved, -n.signal))
        current = next((n for n in st.networks if n.in_use), None)
        if current:
            st.ssid, st.signal = current.ssid, current.signal
        addr = _iface_network(IFACE)
        st.address = str(addr.ip) if addr else ""
    except WifiError as exc:
        st.error = str(exc)
    return st


def _hotspot_status() -> Hotspot:
    uuid = _hotspot_uuid()
    hs = Hotspot(saved=bool(uuid), on=bool(uuid) and _active_wifi_uuid() == uuid)
    if uuid:
        hs.ssid = _get("802-11-wireless.ssid", uuid)
        try:
            hs.password = _get("802-11-wireless-security.psk", uuid, secret=True)
        except WifiError:
            pass
    hs.ssid = hs.ssid or _default_hotspot_ssid()
    hs.address = str(HOTSPOT_ADDR.ip)    # where it will be while off, so the page can say
    if hs.on:
        hs.isolated = _forwarding(IFACE) == "0"
    else:
        hs.password = hs.password or DEFAULT_HOTSPOT_PASSWORD
    return hs


def operation() -> Operation:
    with _op_lock:
        return Operation(**vars(_op))


# ── changes ───────────────────────────────────────────────────────────────────

def validate_password(password: str) -> str:
    """"" if acceptable for WPA-PSK, else the reason. Empty means an open network."""
    if not password:
        return ""
    if len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password):
        return ""
    if not 8 <= len(password) <= 63:
        return "A Wi-Fi password is 8 to 63 characters."
    return ""


def _begin(kind: str, ssid: str) -> None:
    global _op
    with _op_lock:
        if _op.running:
            raise WifiError(f"Busy: still working on {_op.ssid}.")
        _op = Operation(kind=kind, ssid=ssid, state="running")


def _finish(ok: bool, message: str) -> None:
    with _op_lock:
        _op.state = "ok" if ok else "failed"
        _op.message = message
        _op.finished_at = time.time()


def start_connect(ssid: str, password: str, hidden: bool, robot_ip: str) -> None:
    """Start connecting on a thread. Raises WifiError if it cannot start."""
    ok, reason = available()
    if not ok:
        raise WifiError(reason)
    ssid = ssid.strip()
    if not ssid or len(ssid.encode()) > 32:
        raise WifiError("Enter a network name (up to 32 characters).")
    bad = validate_password(password)
    if bad:
        raise WifiError(bad)
    _begin("connect", ssid)
    threading.Thread(target=_connect_body, args=(ssid, password, hidden, robot_ip),
                     name="wifi-connect", daemon=True).start()


def _connect_body(ssid: str, password: str, hidden: bool, robot_ip: str) -> None:
    try:
        ok, msg = _connect(ssid, password, hidden, robot_ip)
    except Exception as exc:  # noqa: BLE001 - reported on the card, never swallowed
        log.exception("wifi connect crashed")
        ok, msg = False, str(exc)
    log.info("wifi connect ssid=%r ok=%s msg=%s", ssid, ok, msg)
    _finish(ok, msg)


def _connect(ssid: str, password: str, hidden: bool, robot_ip: str) -> tuple[bool, str]:
    # Snapshot everything needed to put things back.
    robot_dev = _robot_route_dev(robot_ip)
    robot_net = _iface_network(robot_dev) if robot_dev and robot_dev != IFACE else None
    prev_uuid = _active_wifi_uuid()
    hotspot = _hotspot_uuid()
    from_hotspot = bool(hotspot) and prev_uuid == hotspot
    before = _wifi_profiles()
    before_uuids = {u for u, _ in before}
    saved = [u for u, s in before if s == ssid]

    if saved and not password:
        args = ["--wait", str(CONNECT_TIMEOUT_S), "connection", "up", "uuid", saved[0], "ifname", IFACE]
    elif saved:
        # device-wifi-connect would reuse or duplicate the saved profile in ways that
        # vary by NetworkManager version. Forget first, then enter the new password.
        return False, f"{ssid} is already saved. Forget it first, then enter the new password."
    else:
        args = ["--wait", str(CONNECT_TIMEOUT_S), "device", "wifi", "connect", ssid, "ifname", IFACE]
        if password:
            args += ["password", password]
        if hidden:
            args += ["hidden", "yes"]

    # The radio is either the hotspot or a client, so joining a network ends the hotspot.
    if from_hotspot:
        try:
            _hotspot_off(hotspot)
        except WifiError as exc:
            return False, f"Could not turn the hotspot off to join {ssid}: {exc}"
        if not saved and not hidden:
            _await_ssid(ssid)    # device-wifi-connect needs the network in a scan
    back = "Turned the hotspot back on." if from_hotspot else "Reconnected to the previous Wi-Fi."

    try:
        _nmcli(*args, timeout=CONNECT_TIMEOUT_S + 10)
    except WifiError as exc:
        _undo(before_uuids, prev_uuid, from_hotspot)
        return False, f"Could not connect to {ssid}: {exc}" + (f" {back}" if from_hotspot else "")

    # Wi-Fi must not share the robot's subnet or take over the route to it.
    wifi_net = _iface_network(IFACE)
    if robot_net and wifi_net and wifi_net.network.overlaps(robot_net.network):
        _undo(before_uuids, prev_uuid, from_hotspot)
        return False, (f"{ssid} gave this panel {wifi_net}, which overlaps the robot network "
                       f"{robot_net.network}. {back}")
    if robot_dev and _robot_route_dev(robot_ip) != robot_dev:
        _undo(before_uuids, prev_uuid, from_hotspot)
        return False, f"Joining {ssid} moved the robot route off {robot_dev}. {back}"

    where = f" · {wifi_net.ip}" if wifi_net else ""
    return True, ("Hotspot off. " if from_hotspot else "") + f"Connected to {ssid}{where}."


def _await_ssid(ssid: str, timeout: float = 15) -> None:
    """Wait for `ssid` to show up in a scan after the access point goes down. The
    connect that follows reports "not found" itself, so running out of time is fine."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            out = _nmcli("-t", "-f", "SSID", "device", "wifi", "list", "ifname", IFACE,
                         "--rescan", "auto", timeout=25)
            if any(split_terse(line)[0] == ssid for line in out.splitlines()):
                return
        except WifiError:
            pass     # NetworkManager refuses scans for a moment while the AP goes down
        if time.monotonic() >= deadline:
            return
        time.sleep(1.5)


def _undo(before_uuids: set[str], prev_uuid: str, from_hotspot: bool) -> None:
    """Roll back a failed join. When the join started from the hotspot, the hotspot
    comes back on, and autoconnects again so it survives a power-off as before."""
    if from_hotspot:
        try:
            _nmcli("connection", "modify", "uuid", prev_uuid, "connection.autoconnect", "yes")
        except WifiError as exc:
            log.warning("wifi rollback: could not re-enable hotspot autoconnect: %s", exc)
    _rollback(before_uuids, prev_uuid)


def _rollback(before_uuids: set[str], prev_uuid: str) -> None:
    """Delete Wi-Fi profiles the attempt created, then bring the old network back."""
    try:
        for uuid, _ in _wifi_profiles():
            if uuid not in before_uuids:
                _nmcli("connection", "delete", "uuid", uuid)
    except WifiError as exc:
        log.warning("wifi rollback: could not delete new profile: %s", exc)
    _restore(prev_uuid)


def _restore(prev_uuid: str) -> None:
    """Bring the previously active Wi-Fi profile back up, unless it already is."""
    if prev_uuid and _active_wifi_uuid() != prev_uuid:
        try:
            _nmcli("--wait", str(CONNECT_TIMEOUT_S), "connection", "up", "uuid", prev_uuid,
                   "ifname", IFACE, timeout=CONNECT_TIMEOUT_S + 10)
        except WifiError as exc:
            log.warning("wifi rollback: could not restore previous network: %s", exc)


def forget(ssid: str) -> str:
    """Delete every saved Wi-Fi profile for `ssid`. Returns the result message."""
    ok, reason = available()
    if not ok:
        raise WifiError(reason)
    _begin("forget", ssid)
    try:
        uuids = [u for u, s in _wifi_profiles() if s == ssid]
        for uuid in uuids:
            # _wifi_profiles() lists Wi-Fi profiles only; check again right before deleting.
            if _nmcli("-g", "connection.type", "connection", "show", "uuid", uuid).strip() != WIFI_TYPE:
                continue
            _nmcli("connection", "delete", "uuid", uuid)
        msg = f"Forgot {ssid}." if uuids else f"{ssid} was not saved."
        _finish(True, msg)
        return msg
    except WifiError as exc:
        _finish(False, f"Could not forget {ssid}: {exc}")
        raise


def radio_on() -> None:
    ok, reason = available()
    if not ok:
        raise WifiError(reason)
    _nmcli("radio", "wifi", "on")


# ── hotspot ───────────────────────────────────────────────────────────────────

def start_hotspot(ssid: str, password: str, robot_ip: str) -> None:
    """Start the hotspot on a thread. Raises WifiError if it cannot start."""
    ok, reason = available()
    if not ok:
        raise WifiError(reason)
    ssid = ssid.strip()
    if not ssid or len(ssid.encode()) > 32:
        raise WifiError("Enter a hotspot name (up to 32 characters).")
    if not password:
        raise WifiError("The hotspot needs a password (8 to 63 characters).")
    bad = validate_password(password)
    if bad:
        raise WifiError(bad)
    _begin("hotspot", ssid)
    threading.Thread(target=_hotspot_body, args=(ssid, password, robot_ip),
                     name="wifi-hotspot", daemon=True).start()


def _hotspot_body(ssid: str, password: str, robot_ip: str) -> None:
    try:
        ok, msg = _start_hotspot(ssid, password, robot_ip)
    except Exception as exc:  # noqa: BLE001 - reported on the card, never swallowed
        log.exception("wifi hotspot crashed")
        ok, msg = False, str(exc)
    log.info("wifi hotspot ssid=%r ok=%s msg=%s", ssid, ok, msg)
    _finish(ok, msg)


def _start_hotspot(ssid: str, password: str, robot_ip: str) -> tuple[bool, str]:
    robot_dev = _robot_route_dev(robot_ip)
    robot_net = _iface_network(robot_dev) if robot_dev and robot_dev != IFACE else None
    if robot_net and robot_net.network.overlaps(HOTSPOT_ADDR.network):
        return False, (f"The robot network {robot_net.network} overlaps the hotspot's "
                       f"{HOTSPOT_ADDR.network}. The hotspot was not started.")
    prev_uuid = _active_wifi_uuid()
    uuid = _hotspot_uuid()
    settings = [*HOTSPOT_SETTINGS, "802-11-wireless.ssid", ssid, "802-11-wireless-security.psk", password]
    try:
        if uuid:
            _nmcli("connection", "modify", "uuid", uuid, *settings)
        else:
            _nmcli("connection", "add", "type", "wifi", "ifname", IFACE, "con-name", HOTSPOT_NAME, *settings)
            uuid = _hotspot_uuid()
        _nmcli("--wait", str(CONNECT_TIMEOUT_S), "connection", "up", "uuid", uuid, "ifname", IFACE,
               timeout=CONNECT_TIMEOUT_S + 10)
    except WifiError as exc:
        _hotspot_rollback(uuid, prev_uuid)
        return False, f"Could not start the hotspot: {exc}"

    if robot_dev and _robot_route_dev(robot_ip) != robot_dev:
        _hotspot_rollback(uuid, prev_uuid)
        return False, (f"Starting the hotspot moved the robot route off {robot_dev}. "
                       f"Stopped it and reconnected to the previous Wi-Fi.")
    return True, f"Hotspot {ssid} is on."


def _hotspot_rollback(uuid: str, prev_uuid: str) -> None:
    try:
        _hotspot_off(uuid)
    except WifiError as exc:
        log.warning("wifi hotspot rollback: could not stop the hotspot: %s", exc)
    _restore(prev_uuid)


def _hotspot_off(uuid: str) -> None:
    """Take the hotspot down and keep it from coming back at boot."""
    if not uuid:
        return
    _nmcli("connection", "modify", "uuid", uuid, "connection.autoconnect", "no")
    if _active_wifi_uuid() == uuid:
        _nmcli("connection", "down", "uuid", uuid)


def stop_hotspot() -> str:
    """Stop the hotspot. NetworkManager then rejoins a saved network if one is in range."""
    ok, reason = available()
    if not ok:
        raise WifiError(reason)
    _begin("hotspot-stop", HOTSPOT_NAME)
    try:
        _hotspot_off(_hotspot_uuid())
        msg = "Hotspot off. The panel rejoins a saved network if one is in range."
        _finish(True, msg)
        return msg
    except WifiError as exc:
        _finish(False, f"Could not stop the hotspot: {exc}")
        raise
