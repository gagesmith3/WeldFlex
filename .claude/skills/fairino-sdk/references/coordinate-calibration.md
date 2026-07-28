# Coordinate-system calibration (TCP & work-object)

**Status**: the TCP (tool) 4-point flow is implemented and working in
production. Work-object 3-point calibration (`/operator/calibrate`) is linked
from the UI but **not yet implemented** — it's the next planned feature. Mirror
the working TCP flow rather than designing from scratch: `tcp_calibrate.html`
+ `partials/tcp_calibrate_steps.html` on the frontend, `app.py`'s
`_tcp_calib`/`_tcp_lock`/`_tcp_render()` state-dict pattern, and
`robot_service.py`'s `tcp_enable_drag()`/`tcp_record_point()`/
`tcp_compute_and_apply()` methods on the backend. The full app-side pattern
(state dict, routes, partial) is documented in the `weldflex-app` skill's
`references/state-and-session.md`; this file only covers the SDK calls
themselves.

## Tool/TCP 4-point flow (working reference)

1. **`SetTcp4RefPoint(self, point_num)`** — `Robot.py:4760`. `point_num ∈ [1,4]`.
   Bare int return. Call once per physically-touched reference point.
2. **`ComputeTcp4(self)`** — `Robot.py:4783`. No params. Computes from the 4
   recorded points. Returns `(0, [x,y,z,rx,ry,rz])` on success, `(err, None)`
   on failure.
3. **`SetToolCoord(self, id, t_coord, type, install, toolID, loadNum)`** —
   `Robot.py:4813`. This is the "apply" step, fed the pose `ComputeTcp4`
   returned.
   - `id ∈ [1,15]` — **1-indexed** tool-coord slot.
   - `t_coord` = `[x,y,z,rx,ry,rz]` in mm/°.
   - `type`: `0`=tool coord, `1`=sensor coord.
   - `install`: `0`=robot end, `1`=external.
   - Bare int return.

A 6-point analog exists (`SetToolPoint`/`ComputeTool`, points `[1,6]`, same
shape) if higher precision is ever needed. A points-based alternative that
skips the physical-move step entirely —
**`ComputeToolCoordWithPoints(method, pos, ...)`** (`Robot.py:~13700`),
`method`: `0`=four-point, `1`=six-point, taking joint-position arrays
directly — is also available.

## Work-object 3-point flow (to build)

1. **`SetWObjCoordPoint(self, point_num)`** — `Robot.py:4970`. `point_num ∈ [1,3]`.
2. **`ComputeWObjCoord(self, method, refFrame)`** — `Robot.py:4994`.
   - `method`: `0` = origin→x-axis→z-axis, `1` = origin→x-axis→xy-plane
     (confirmed identically in the Chinese docstring and English PDF
     §2.1.6.16/§2.4.6.16).
   - `refFrame` — "reference coordinate system." **Never enumerated** in
     either doc; every official SDK example passes `refFrame=0` (base
     coordinate system) uniformly. Treat `0` as the safe default.
   - Returns `(0, [x,y,z,rx,ry,rz])` / `(err, None)`.
3. **`SetWObjCoord(self, id, coord, refFrame)`** — `Robot.py:5023`.
   - `id ∈ [0,14]` — **0-indexed**. This is the opposite convention from
     `SetToolCoord`'s 1–15 — an easy off-by-one if you're used to the tool
     flow.
   - `coord` = `[x,y,z,rx,ry,rz]` mm/°. Bare int return.

A points-based alternative that skips `SetWObjCoordPoint`'s physical-move
step — **`ComputeWObjCoordWithPoints(self, method, pos, refFrame)`**
(`Robot.py:13775`) — takes `pos` as a list of 3 `[x,y,z,rx,ry,rz]` TCP poses
directly, with the same `method`/`refFrame` meaning and return shape as
`ComputeWObjCoord`.

### Which wobj slot to target

**Use `id = 2`.** `programs/feedCycle.lua`, `programs/testCycle.lua`, and
`programs/libertytest.lua` all set `wobj = 2`, and `feedCycle.lua` explicitly
comments `-- Work Coordinate System: WObjCoord2`. This is the slot every
production Lua program on the robot already expects — writing a new work
object to a different slot would silently decouple calibration from what the
weld programs actually use.

## `DragTeachSwitch(self, state)` — `Robot.py:2846`

`state`: `0`=exit, `1`=enter drag-teach mode. This is how you physically move
the robot to touch each reference point without fighting its motors: enter
drag mode, hand-position the robot at the point, exit drag mode, **then** call
`SetTcp4RefPoint`/`SetWObjCoordPoint` to capture the now-static pose.

(The bundled SDK examples instead use `MoveJ` to programmatically drive to
known joint positions before recording each point — both are valid; drag-teach
is for when you don't already know the target coordinates, which is the case
here.) `robot_service.py`'s `tcp_enable_drag()`/`tcp_record_point()` implement
exactly this drag-teach pattern — the pattern to mirror for the work-object
methods.

**Sequencing note** from the official PDF (C++ §2.1.3.6, mirrored in
`NewTest0609.py:17-20`): `Mode(1)` (manual mode) is called *before*
`DragTeachSwitch(1)`, with a ~1s sleep in between. Not enforced by the SDK,
but consistent enough across every official example to treat as a soft
precondition.

## Stale example warning

`windows/example/TestSetCommand.py:132-138` calls
`robot.ComputeWObjCoord(method=0)` (missing the required `refFrame` arg) and
`robot.SetWObjCoord(id=4, w_coord=wobjcoord)` (wrong kwarg name — the actual
param is `coord`, and `refFrame` is also missing). As written, this would
raise `TypeError` against the current `Robot.py` signatures. **Don't copy
bundled example scripts verbatim** — cross-check against the live
signature/docstring in `Robot.py` first.
