# Response conventions — pick one when adding a route

Three deliberate patterns exist. Pick based on what kind of route you're
adding — don't invent a fourth.

## Pattern A — command-result toast

For simple one-shot actions (settings, FT setup, diagnostics resets, wifi,
run/pause/resume):

```python
try:
    robot.reset_errors()
    ok, payload = True, {}
except Exception as e:
    ok, payload = False, {"error": str(e)}
return render_template("partials/command_result.html", ok=ok, title="Reset Errors", payload=payload)
```

`partials/command_result.html` renders a self-dismissing toast (auto-clears
after 3.5s on success, 9s on error) and shows `payload.error` inline if
`not ok`. Use this for routes that don't need to persist any state between
requests — a single action with a pass/fail result.

## Pattern B — in-state-dict error key, displayed inline

For multi-step wizards (calibration flows):

```python
except Exception as e:
    with _tcp_lock:
        _tcp_calib["drag_point"] = None
        _tcp_calib["drag_error"] = str(e)
```

The partial then renders each `*_error` key inline, independently, per step:
```jinja
{% if drag_error %}<p class="calib-error-note">Drag error: {{ drag_error }}</p>{% endif %}
```
(repeated for `record_error`, `apply_error` as separate slots). Use this when
a wizard has multiple independent steps that can each fail separately and the
operator needs to see which step failed without losing progress on the others.

**Known gap**: `_run_session["error_msg"]` is set on failure in
`ui_operator_run` (`app.py:527-530`) and the cycle-advance branch of
`ui_operator_current_job` (`app.py:582-585`) — following Pattern B — but
`partials/current_job.html` **never reads `session.error_msg`**, only shows a
generic `state-badge--error` badge with no message text. If you touch the run
session's error path, either wire up the display or note in your change that
you're aware it's still silent. See `../../sdk-alignment-findings.md`.

## Pattern C — raw status-code endpoints (JS-driven polling loops)

For routes driven by client-side JS rather than htmx (e.g. `jog.html`'s
pointerdown/pointerup jog loop), skip the rendered-partial response entirely:

```python
@app.route("/ui/jog/move", methods=["POST"])
def ui_jog_move():
    try:
        robot.jog_step(...)
        return ("", 204)
    except Exception as e:
        return (str(e), 500)
```

The frontend JS only checks `res.ok` (any 2xx) to decide whether to continue
its loop or stop — it never parses a response body. Use this pattern only for
endpoints driven by raw `fetch()` calls in a `<script>` block, not for
anything targeted by `hx-get`/`hx-post` (those need a real partial to swap
in).

## Oddball: out-of-band swap

`partials/diagnostics_readout.html:92-96` uses an HTMX out-of-band swap
(`hx-swap-oob="innerHTML"` on `#diagnostics-error`) to push an error into a
sibling panel. This is unique to the diagnostics page — treat it as a one-off
for that specific layout, not a fourth general convention to reuse elsewhere.
