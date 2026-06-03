# WeldFlex — Project Goals & Completion Status

> Last updated: 2026-06-03

---

## Legend
- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete

---

## 1. Lua Scripts
| Status | Goal | Notes |
|--------|------|-------|
| `[x]` | Add `studCycle.lua` to repo | Done 2026-06-03 — `robot/lua/studCycle.lua` |
| `[x]` | Validate generated `studs_data.lua` format before upload | Done 2026-06-03 — empty guard + finite check + ±2000 mm limit in `lua_builder.validate_studs` |
| `[ ]` | Add error handling inside `studCycle.lua` | Handle missing data file, out-of-bounds stud, weld fault |
| `[ ]` | Support per-stud weld parameters (dwell, force, etc.) | Currently all studs use identical robot program settings |
| `[ ]` | Replace `weld.lua` stub with digital IO weld trigger | Current stub just moves Z down/up; real welder needs IO sequence |

---

## 2. Calibration Tools
| Status | Goal | Notes |
|--------|------|-------|
| `[x]` | New `/calibration` page | Done 2026-06-03 — live position, jog, origin capture, dry run |
| `[x]` | Jog controls (X/Y/Z, step size selector) | Done 2026-06-03 — StartJOG via SDK, 0.1/1/5/10/50 mm steps |
| `[x]` | Set work origin (capture current robot position as 0,0) | Done 2026-06-03 — GetActualTCPPose → data/calibration.json |
| `[ ]` | Tool offset calibration | TCP calibration relative to robot flange |
| `[x]` | Dry-run / test-move | Done 2026-06-03 — studCycleDryRun.lua, 30% speed, no weld |
| `[x]` | Save/restore calibration data | Done 2026-06-03 — persisted to data/calibration.json |

---

## 3. Home Page & Live Run UX
| Status | Goal | Notes |
|--------|------|-------|
| `[x]` | 75/25 split layout (run card / nav strip) | Done 2026-06-03 |
| `[ ]` | Highlight current weld point live on SVG during run | Animate active stud as robot works through sequence |
| `[ ]` | Quick-launch recipe from home without navigating to Part Library | Recipe picker directly on home screen |
| `[ ]` | Estimated time remaining for active batch | Based on average cycle time |

---

## 4. SPA / Operator UI Rework
| Status | Goal | Notes |
|--------|------|-------|
| `[ ]` | Merge Part Library into Part Designer | One screen to pick, edit, preview, and run — eliminate separate library page |
| `[x]` | Toast/notification system | Done 2026-06-03 — fixed-bottom overlay toasts, auto-dismiss, standard via `#toast-rack` |
| `[ ]` | Operator-friendly error recovery | Clear next-action guidance when robot faults or connection drops |
| `[ ]` | Keyboard/touch shortcut for E-STOP | Hardware-level accessible stop, not buried in header |

---

## 5. Run History & Reporting
| Status | Goal | Notes |
|--------|------|-------|
| `[ ]` | Log each completed cycle | Timestamp, part name, stud count, pass/fail |
| `[ ]` | Run history view | Filterable/scrollable table of past runs |
| `[ ]` | Cycle time tracking | Average, min, max per part |
| `[ ]` | Export run log (CSV) | For QC records |

---

## 6. Backend & Reliability
| Status | Goal | Notes |
|--------|------|-------|
| `[x]` | Robust cycle-completion detection | Done 2026-06-03 — 30s lost-connection timeout in `RunStateManager._refresh_cycle_completion` |
| `[x]` | Lost-connection recovery | Done 2026-06-03 — 5s timeout on all SDK calls via `_run_with_timeout`; resets connection on timeout |
| `[ ]` | Pre-run health check | Verify robot is online and ready before uploading program |
| `[ ]` | FTP upload reliability | Retry logic, verify file was written before ProgramRun |

---

## 7. Kiosk / Deployment
| Status | Goal | Notes |
|--------|------|-------|
| `[x]` | Plymouth boot splash | Done 2026-06-03 — WeldFlex logo on dark background |
| `[x]` | Auto git-pull on boot | Done — `weldflex-update.service` with retry + connectivity check |
| `[x]` | Git SHA in BETA badge | Done 2026-06-03 — visible in header for update verification |
| `[x]` | Chromium auto-detect binary | Done 2026-06-03 — tries `chromium` then `chromium-browser` |
| `[x]` | Custom in-browser virtual keyboard | Done 2026-06-03 — replaces onboard; `data-kbd="num\|alpha"` on any input |
| `[ ]` | Screen timeout / idle lock | Blank screen after N minutes, tap to wake |
