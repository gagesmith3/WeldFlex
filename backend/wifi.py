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
    kind: str = ""               # "connect" | "forget" | ""
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


def _wifi_profiles() -> list[tuple[str, str]]:
    """(uuid, ssid) for every saved Wi-Fi profile. Other profile types never appear."""
    out = []
    for line in _nmcli("-t", "-f", "UUID,TYPE", "connection", "show").splitlines():
        parts = split_terse(line)
        if len(parts) >= 2 and parts[1] == WIFI_TYPE:
            ssid = _nmcli("-g", "802-11-wireless.ssid", "connection", "show", "uuid", parts[0]).strip()
            out.append((parts[0], ssid))
    return out


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
        saved = {ssid for _, ssid in _wifi_profiles()}
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

    try:
        _nmcli(*args, timeout=CONNECT_TIMEOUT_S + 10)
    except WifiError as exc:
        _rollback(before_uuids, prev_uuid)
        return False, f"Could not connect to {ssid}: {exc}"

    # Wi-Fi must not share the robot's subnet or take over the route to it.
    wifi_net = _iface_network(IFACE)
    if robot_net and wifi_net and wifi_net.network.overlaps(robot_net.network):
        _rollback(before_uuids, prev_uuid)
        return False, (f"{ssid} gave this panel {wifi_net}, which overlaps the robot network "
                       f"{robot_net.network}. Reconnected to the previous Wi-Fi.")
    if robot_dev and _robot_route_dev(robot_ip) != robot_dev:
        _rollback(before_uuids, prev_uuid)
        return False, f"Joining {ssid} moved the robot route off {robot_dev}. Reconnected to the previous Wi-Fi."

    where = f" · {wifi_net.ip}" if wifi_net else ""
    return True, f"Connected to {ssid}{where}."


def _rollback(before_uuids: set[str], prev_uuid: str) -> None:
    """Delete Wi-Fi profiles the attempt created, then bring the old network back."""
    try:
        for uuid, _ in _wifi_profiles():
            if uuid not in before_uuids:
                _nmcli("connection", "delete", "uuid", uuid)
    except WifiError as exc:
        log.warning("wifi rollback: could not delete new profile: %s", exc)
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
