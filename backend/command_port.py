"""The controller's port-8080 command channel — how Pause/Resume/Stop reach a
robot that has stopped answering XML-RPC.

The controller stops answering XML-RPC (:20003) for the whole of a force
operation (FT_FindSurface, the press and the hold), while the robot runs on.
ProgramPause/ProgramResume/ProgramStop ride XML-RPC, so the app's buttons went
dead in exactly the window an operator most wants them (live on the Pi,
2026-09-23: two Pauses during the press timed out, the run went on). The
pendant and the vendor web app never go through :20003, which is why they
still worked.

Port 8080 speaks the vendor's frame protocol (docs/Collaborative Robot
Controller Communication Command Protocol User Manual.txt §1.1):

    /f/bIII{CNT}III{CMD_ID}III{LEN}III{DATA}III/b/f

and answers `/f/bIII{CNT}III{CMD_ID}III1III{result}III/b/f`, result "1" on
success. The SDK uses it for `PauseMotion` (103 PAUSE), `ResumeMotion`
(104 RESUME) and `StopMove` (102 STOP), but through `send_message`, which has
no socket timeout and sits behind `@xmlrpc_timeout` — that decorator refuses
the call whenever the link reads disconnected, i.e. in the very window this is
for. So the frames are sent here directly, one short-lived connection per
command, with hard timeouts, and never through the link's SDK worker (which
may itself be stuck behind an XML-RPC call).

102 STOP is documented as "Stop the robot from moving"; 103/104 appear only in
the SDK. Neither the manual nor the SDK says how they differ from the
Program* calls, so callers prefer XML-RPC while it answers and use this only
when it does not.
"""

from __future__ import annotations

import itertools
import logging
import socket
import threading

log = logging.getLogger(__name__)

COMMAND_PORT = 8080
CONNECT_TIMEOUT_S = 2.0
REPLY_TIMEOUT_S = 3.0

CMD_STOP = 102
CMD_PAUSE = 103
CMD_RESUME = 104

# The vendor's own content strings for each command (SDK Robot.py and the manual).
_CONTENT = {CMD_STOP: "STOP", CMD_PAUSE: "PAUSE", CMD_RESUME: "RESUME"}

_counter = itertools.count(1)
_counter_lock = threading.Lock()


class CommandPortError(RuntimeError):
    """The command was not delivered, or the controller refused it."""


def _next_count() -> int:
    with _counter_lock:
        return next(_counter) % 65536  # the manual's CNT is a uint16


def build_frame(count: int, cmd_id: int, content: str) -> str:
    return f"/f/bIII{count}III{cmd_id}III{len(content)}III{content}III/b/f"


def parse_reply(reply: str, cmd_id: int) -> bool:
    """True when the controller accepted `cmd_id`. Raises on a malformed reply."""
    start = reply.find("/f/b")
    end = reply.find("/b/f", start + 4)
    if start < 0 or end < 0:
        raise CommandPortError(f"malformed reply {reply[:80]!r}")
    fields = reply[start:end + 4].split("III")
    # ['/f/b', CNT, CMD_ID, LEN, DATA, '/b/f']
    if len(fields) != 6:
        raise CommandPortError(f"malformed reply {reply[:80]!r}")
    if fields[2].strip() != str(cmd_id):
        raise CommandPortError(f"reply is for command {fields[2]!r}, not {cmd_id}")
    return fields[4].strip() == "1"


def send_command(
    ip: str,
    cmd_id: int,
    port: int = COMMAND_PORT,
    connect_timeout_s: float = CONNECT_TIMEOUT_S,
    reply_timeout_s: float = REPLY_TIMEOUT_S,
) -> None:
    """Send one command frame and wait for its reply. Raises CommandPortError."""
    content = _CONTENT[cmd_id]
    frame = build_frame(_next_count(), cmd_id, content)
    try:
        sock = socket.create_connection((ip, port), timeout=connect_timeout_s)
    except OSError as exc:
        raise CommandPortError(f"connect to {ip}:{port} failed: {exc}") from exc
    try:
        sock.settimeout(reply_timeout_s)
        sock.sendall(frame.encode("utf-8"))
        buf = b""
        while b"/b/f" not in buf:
            chunk = sock.recv(1024)
            if not chunk:
                break
            buf += chunk
    except OSError as exc:
        raise CommandPortError(f"{content} on {ip}:{port}: {exc}") from exc
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if not buf:
        raise CommandPortError(f"{content} on {ip}:{port}: no reply")
    if not parse_reply(buf.decode("utf-8", errors="replace"), cmd_id):
        raise CommandPortError(f"controller refused {content} (port {port})")
    log.info("command port %s accepted by %s", content, ip)
