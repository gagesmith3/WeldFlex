# Force Sensor Capability Notes

## Question
Can this FAIRINO setup hold about 30 lbf contact force during linear weld travel while maintaining quality and cycle time?

## Short Answer
Yes, based on local SDK evidence, the platform supports constant-force and impedance-style control modes that can be used for this objective.

30 lbf is approximately 133.4 N.

## Local Evidence

- Constant force control API exists:
  - FT_Control in fairino-python-sdk-main/linux/fairino/Robot.py:7751
  - Force/torque target units documented as N or Nm in fairino-python-sdk-main/linux/fairino/Robot.py:7734

- Collision/force guarding exists with configurable thresholds around setpoint:
  - FT_Guard docs and signature in fairino-python-sdk-main/linux/fairino/Robot.py:7694
  - Range definition around force_torque +/- threshold in fairino-python-sdk-main/linux/fairino/Robot.py:7702

- Surface seek using force threshold exists:
  - FT_FindSurface with terminate force threshold in N in fairino-python-sdk-main/linux/fairino/Robot.py:7950

- Compliance mode exists with force threshold in N:
  - FT_ComplianceStart in fairino-python-sdk-main/linux/fairino/Robot.py:7999

- Cartesian/Joint impedance control exists with force threshold in N:
  - ImpedanceControlStartStop docs and signature in fairino-python-sdk-main/linux/fairino/Robot.py:15949
  - ImpedanceControlStartStop implementation in fairino-python-sdk-main/linux/fairino/Robot.py:15962

- Example flow showing FT control enabled before linear motion and disabled after:
  - fairino-python-sdk-main/windows/example/TestForceControlCommand.py:93
  - fairino-python-sdk-main/windows/example/TestForceControlCommand.py:96

- Current WeldFlex backend currently configures collision strategy but does not yet call FT or impedance APIs:
  - backend/robot_service.py:209
  - backend/robot_service.py:240

## Practical Interpretation

- Primary mechanism for holding force during weld travel:
  - FT_Control with selected active axis and a target force near 133 N on the contact axis.

- Useful supporting functions:
  - FT_Guard for safety envelope checks.
  - FT_FindSurface for consistent contact acquisition.
  - ImpedanceControlStartStop or FT_ComplianceStart for controlled compliance where path disturbance handling is needed.

## Confidence
Medium-High.

## Gaps

- No live robot commissioning test was run in this session.
- Exact stable gains for your torch, fixture stiffness, and weld process are not validated here.
- Local PDF exists at docs/fairino-doc-en-readthedocs-io-en-latest.pdf, but direct text extraction was not available with current tooling in this run.

## Next Checks

1. Dry-run at low force first, then ramp toward 133 N while monitoring path error and arc stability.
2. Confirm axis sign and coordinate frame mapping for the active force axis.
3. Add FT or impedance calls into WeldFlex runtime flow when ready for implementation.
