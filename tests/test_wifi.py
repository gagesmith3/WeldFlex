"""Settings → Wi-Fi: backend/wifi.py against a fake nmcli/ip host.

The property these tests protect: a Wi-Fi change never touches the robot's eth0
profile, and a connect that fails or lands on the robot's subnet is rolled back
to the previous network. The fake answers only the commands wifi.py issues and
records every call, so a test can check that no eth0 profile was touched.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

import wifi

ROBOT_IP = "192.168.57.2"
ETH_UUID = "eth-uuid-0000"


class FakeHost:
    def __init__(self):
        self.calls = []
        # uuid -> (type, ssid, device or None)
        self.profiles = {
            ETH_UUID: ("802-3-ethernet", "", "eth0"),
            "home-uuid": ("802-11-wireless", "HomeNet", "wlan0"),
        }
        self.scan = [("*", "HomeNet", "70", "WPA2"), ("", "ShopNet", "82", "WPA2"),
                     ("", "ShopNet", "40", "WPA2"), ("", "Guest", "30", ""),
                     ("", "", "90", "WPA2"), ("", "Corp:5G", "50", "WPA2 802.1X")]
        self.wlan_addr = ("192.168.1.132", 24)
        self.connect_result = "ok"        # "ok" | "fail" | ("addr", ip, prefix)
        self.robot_dev_after = "eth0"

    # subprocess.run replacement
    def run(self, args, **_kw):
        self.calls.append(list(args))
        out, rc, err = self._answer(list(args))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    def _active_wifi(self):
        return next((u for u, (t, _, d) in self.profiles.items() if t == "802-11-wireless" and d), "")

    def _answer(self, a):
        if a[:3] == ["ip", "-j", "route"]:
            dev = "eth0"
            if any(c[:1] == ["nmcli"] and "connect" in c for c in self.calls[:-1]):
                dev = self.robot_dev_after
            return json.dumps([{"dst": ROBOT_IP, "dev": dev}]), 0, ""
        if a[:4] == ["ip", "-j", "-4", "addr"]:
            dev = a[-1]
            ip, pfx = ("192.168.57.100", 24) if dev == "eth0" else self.wlan_addr
            return json.dumps([{"addr_info": [{"family": "inet", "local": ip, "prefixlen": pfx}]}]), 0, ""
        assert a[0] == "nmcli", a
        a = a[1:]
        if a[:1] == ["--wait"]:
            a = a[2:]
        if a == ["radio", "wifi"]:
            return "enabled\n", 0, ""
        if a[:4] == ["-t", "-f", "UUID,TYPE", "connection"]:
            return "".join(f"{u}:{t}\n" for u, (t, _, _) in self.profiles.items()), 0, ""
        if a[:4] == ["-t", "-f", "UUID,TYPE,DEVICE", "connection"]:
            return "".join(f"{u}:{t}:{d}\n" for u, (t, _, d) in self.profiles.items() if d), 0, ""
        if a[:2] == ["-g", "802-11-wireless.ssid"]:
            return self.profiles[a[-1]][1] + "\n", 0, ""
        if a[:2] == ["-g", "connection.type"]:
            return self.profiles[a[-1]][0] + "\n", 0, ""
        if a[:3] == ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY"]:
            esc = lambda s: s.replace("\\", "\\\\").replace(":", "\\:")
            return "".join(":".join(esc(x) for x in row) + "\n" for row in self.scan), 0, ""
        if a[:3] == ["connection", "delete", "uuid"]:
            self.profiles.pop(a[3])
            return "", 0, ""
        if a[:3] == ["connection", "up", "uuid"]:
            self._activate(a[3])
            return "", 0, ""
        if a[:3] == ["device", "wifi", "connect"]:
            ssid = a[3]
            new = f"new-{ssid}"
            self.profiles[new] = ("802-11-wireless", ssid, None)
            if self.connect_result == "fail":
                return "", 4, "Error: Connection activation failed: Secrets were required, but not provided."
            if isinstance(self.connect_result, tuple):
                self.wlan_addr = self.connect_result[1:]
            self._activate(new)
            return "", 0, ""
        raise AssertionError(f"unexpected nmcli call: {a}")

    def _activate(self, uuid):
        for u, (t, s, d) in list(self.profiles.items()):
            if t == "802-11-wireless":
                self.profiles[u] = (t, s, None)
        t, s, _ = self.profiles[uuid]
        self.profiles[uuid] = (t, s, "wlan0")


@pytest.fixture
def host(monkeypatch):
    fake = FakeHost()
    monkeypatch.setattr(wifi.sys, "platform", "linux")
    monkeypatch.setattr(wifi.shutil, "which", lambda _n: "/usr/bin/nmcli")
    monkeypatch.setattr(wifi.subprocess, "run", fake.run)
    monkeypatch.setattr(wifi, "_op", wifi.Operation())
    return fake


def _no_eth_changes(fake):
    for call in fake.calls:
        if call[0] == "nmcli" and ETH_UUID in call:
            assert not any(v in call for v in ("delete", "modify", "up", "down")), call
        assert "eth0" not in call or call[0] == "ip", call


def test_split_terse_unescapes_colons_and_backslashes():
    assert wifi.split_terse(r"*:Corp\:5G:50:WPA2") == ["*", "Corp:5G", "50", "WPA2"]
    assert wifi.split_terse(r"a\\b:c") == ["a\\b", "c"]


def test_status_dedupes_sorts_and_skips_hidden(host):
    st = wifi.status(ROBOT_IP)
    names = [n.ssid for n in st.networks]
    assert names == ["HomeNet", "ShopNet", "Corp:5G", "Guest"]   # current, then by signal
    shop = st.networks[1]
    assert shop.signal == 82 and shop.secured and not shop.saved
    assert st.networks[2].enterprise
    assert not st.networks[3].secured
    assert st.ssid == "HomeNet" and st.address == "192.168.1.132"
    assert st.robot_dev == "eth0" and st.robot_ok
    _no_eth_changes(host)


def test_unavailable_on_windows(monkeypatch):
    monkeypatch.setattr(wifi.sys, "platform", "win32")
    st = wifi.status(ROBOT_IP)
    assert not st.available and "kiosk" in st.reason


def test_connect_new_network(host):
    ok, msg = wifi._connect("ShopNet", "hunter2hunter2", False, ROBOT_IP)
    assert ok, msg
    assert host.profiles["new-ShopNet"][2] == "wlan0"
    assert "home-uuid" in host.profiles            # the old network stays saved
    _no_eth_changes(host)


def test_wrong_password_deletes_new_profile_and_restores_previous(host):
    host.connect_result = "fail"
    ok, msg = wifi._connect("ShopNet", "wrongpass1", False, ROBOT_IP)
    assert not ok and "Wrong password" in msg
    assert "new-ShopNet" not in host.profiles
    assert host.profiles["home-uuid"][2] == "wlan0"
    _no_eth_changes(host)


def test_wifi_on_robot_subnet_is_rolled_back(host):
    host.connect_result = ("addr", "192.168.57.40", 24)
    ok, msg = wifi._connect("ShopNet", "hunter2hunter2", False, ROBOT_IP)
    assert not ok and "overlaps the robot network" in msg
    assert "new-ShopNet" not in host.profiles
    assert host.profiles["home-uuid"][2] == "wlan0"
    _no_eth_changes(host)


def test_robot_route_moving_off_eth0_is_rolled_back(host):
    host.robot_dev_after = "wlan0"
    ok, msg = wifi._connect("ShopNet", "hunter2hunter2", False, ROBOT_IP)
    assert not ok and "moved the robot route" in msg
    assert "new-ShopNet" not in host.profiles


def test_saved_network_reconnects_without_password(host):
    host.profiles["shop-uuid"] = ("802-11-wireless", "ShopNet", None)
    ok, _ = wifi._connect("ShopNet", "", False, ROBOT_IP)
    assert ok
    assert ["nmcli", "--wait", "30", "connection", "up", "uuid", "shop-uuid", "ifname", "wlan0"] in host.calls


def test_saved_network_with_new_password_asks_to_forget_first(host):
    ok, msg = wifi._connect("HomeNet", "newpassword", False, ROBOT_IP)
    assert not ok and "Forget it first" in msg
    assert not any("connect" in c or "delete" in c for c in host.calls if c[0] == "nmcli")


def test_hidden_network_passes_hidden_flag(host):
    wifi._connect("Secret", "hunter2hunter2", True, ROBOT_IP)
    connect = next(c for c in host.calls if "connect" in c)
    assert connect[-2:] == ["hidden", "yes"] and connect[connect.index("ifname") + 1] == "wlan0"


def test_forget_only_deletes_wifi_profiles(host):
    # An ethernet profile that somehow reports the same SSID must survive.
    host.profiles[ETH_UUID] = ("802-3-ethernet", "HomeNet", "eth0")
    msg = wifi.forget("HomeNet")
    assert msg == "Forgot HomeNet."
    assert ETH_UUID in host.profiles and "home-uuid" not in host.profiles


@pytest.mark.parametrize("pw,ok", [("", True), ("short", False), ("a" * 8, True),
                                   ("a" * 63, True), ("g" * 64, False), ("ab" * 32, True)])
def test_validate_password(pw, ok):
    assert (wifi.validate_password(pw) == "") is ok


def test_only_one_operation_at_a_time(host, monkeypatch):
    monkeypatch.setattr(wifi.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    wifi.start_connect("ShopNet", "hunter2hunter2", False, ROBOT_IP)
    with pytest.raises(wifi.WifiError, match="Busy"):
        wifi.start_connect("Guest", "", False, ROBOT_IP)


def test_timeout_is_reported_not_raised(host, monkeypatch):
    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout"))
    monkeypatch.setattr(wifi.subprocess, "run", slow)
    st = wifi.status(ROBOT_IP)
    assert st.available and "timed out" in st.error


# ── routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def wifi_app(monkeypatch, host):
    import importlib
    from robot_service import WeldFlexRobotService
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    state = SimpleNamespace(active=False)
    monkeypatch.setattr(module, "job", SimpleNamespace(snapshot=lambda: state))
    # The fake host answers route queries for any address, so the live robot_ip is fine.
    started = []
    monkeypatch.setattr(wifi, "start_connect", lambda *a: started.append(a))
    return SimpleNamespace(client=module.app.test_client(), job_state=state, started=started,
                           robot_ip=module.robot.robot_ip)


def test_settings_page_and_card_render(wifi_app):
    page = wifi_app.client.get("/operator/settings").get_data(as_text=True)
    assert 'hx-get="/ui/wifi/card"' in page and 'id="wifi-modal"' in page
    card = wifi_app.client.get("/ui/wifi/card").get_data(as_text=True)
    assert "HomeNet" in card and "ShopNet" in card and "Other network" in card
    assert "Not affected by Wi-Fi changes" in card


def test_connect_is_refused_while_a_job_is_active(wifi_app):
    wifi_app.job_state.active = True
    card = wifi_app.client.post("/ui/wifi/connect", data={"ssid": "ShopNet", "password": "x" * 8})
    assert "A job is running" in card.get_data(as_text=True)
    assert wifi_app.started == []


def test_connect_starts_when_idle(wifi_app):
    wifi_app.client.post("/ui/wifi/connect", data={"ssid": "Secret", "password": "x" * 8, "hidden": "1"})
    assert wifi_app.started == [("Secret", "x" * 8, True, wifi_app.robot_ip)]
