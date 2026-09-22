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

**This gap is closed.** `ui_operator_run`/`ui_operator_current_job`/
`_run_session` no longer exist — they went with the poll-driven run session
`job_manager.py` replaced (see `state-and-session.md`). The run's error now
lives on `JobSnapshot.error` (set by the manager on failure) and
`partials/current_job.html` reads it directly: `{% if job.error %}<p
class="current-job-error">{{ job.error }}</p>{% endif %}`. A still-current
example of Pattern B's per-step inline error is `_tcp_calib`'s
`drag_error`/`record_error`/`apply_error`, shown above.

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

`partials/diagnostics_readout.html`'s `#diagnostics-error` block uses an HTMX out-of-band swap
(`hx-swap-oob="innerHTML"` on `#diagnostics-error`) to push an error into a
sibling panel. This is unique to the diagnostics page — treat it as a one-off
for that specific layout, not a fourth general convention to reuse elsewhere.
