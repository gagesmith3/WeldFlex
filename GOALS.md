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
| `[ ]` | Add `studCycle.lua` to repo | Currently lives only on robot controller — needs to be versioned |
| `[ ]` | Validate generated `studs_data.lua` format before upload | Bounds check, non-empty guard, format assertion |
| `[ ]` | Add error handling inside `studCycle.lua` | Handle missing data file, out-of-bounds stud, weld fault |
| `[ ]` | Support per-stud weld parameters (dwell, force, etc.) | Currently all studs use identical robot program settings |

---

## 2. Calibration Tools
| Status | Goal | Notes |
|--------|------|-------|
| `[ ]` | New `/calibration` page | Dedicated operator calibration workspace |
| `[ ]` | Jog controls (X/Y/Z, step size selector) | Manual robot movement from UI |
| `[ ]` | Set work origin (capture current robot position as 0,0) | Touch-off workflow |
| `[ ]` | Tool offset calibration | TCP calibration relative to robot flange |
| `[ ]` | Dry-run / test-move | Move to all stud positions in sequence without welding |
| `[ ]` | Save/restore calibration data | Persist offsets across restarts |

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
| `[ ]` | Toast/notification system | Replace global command-result banner with transient, non-blocking toasts |
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
| `[ ]` | Robust cycle-completion detection | Timeout if robot goes silent; don't hang on lost connection |
| `[ ]` | Lost-connection recovery | Auto-reconnect with status feedback; surface actionable error |
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
| `[ ]` | Onboard virtual keyboard auto-show on input focus | Installed, GSettings override applied — needs verification |
| `[ ]` | Screen timeout / idle lock | Blank screen after N minutes, tap to wake |
