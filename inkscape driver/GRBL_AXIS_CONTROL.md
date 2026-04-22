# Grbl Axis Control Guide

This note documents the Grbl axis controls added to AxiDraw.

## Scope

- Grbl firmware settings (`$3`, `$23`) for direction and homing direction masks
- Software coordinate mapping (`swap X/Y`, `invert X`, `invert Y`) applied in motion output

## Where To Use

- Inkscape extension: `AxiDraw Control` -> `Manual` tab
- CLI: `axicli ... -m manual -M axis_read|axis_apply`

## Manual Commands

- `axis_read`
  - Reads `$$` and reports:
    - `$3` (DirInvert)
    - `$23` (HomingDirInvert)
  - Also prints software mapping switches.

- `axis_apply`
  - Writes `$3` and `$23` if provided.
  - Applies software mapping switches to runtime motion path.

## Mask Bits

Both `$3` and `$23` use bit masks:

- bit0 = X axis
- bit1 = Y axis
- bit2 = Z axis

Examples:

- `1` -> X only
- `2` -> Y only
- `3` -> X + Y
- `4` -> Z only
- `7` -> X + Y + Z

## UI Priority Rules

In `axis_apply`:

1. If any checkbox bits are selected, checkbox-composed mask is used.
2. Otherwise, integer mask fields are used (`grbl_set_dir_mask`, `grbl_set_homing_dir_mask`).
3. Use `-1` to skip writing one of the masks.

## Software Mapping Order

Outgoing motion mapping:

1. swap X/Y
2. invert X
3. invert Y

Incoming status cache mapping uses the inverse operation so logical coordinates remain consistent.

## Safety Notes

- Use small test moves after any mask/mapping update.
- Confirm travel direction before long plots.
- Homing mask changes (`$23`) only matter when homing is enabled and limit switches are valid.

## Pen-Change Flow (Layer Pause)

When a layer pause marker is encountered and `manual_pen_change` is enabled:

1. Current XY position is saved.
2. Pen is raised.
3. Machine moves to Home corner when `pen_change_to_home=True`.
4. User confirmation is requested when `pen_change_prompt=True`.
5. On confirmation, machine returns to saved XY and plotting continues.
6. On cancel/failure, plotting enters normal pause flow and can be resumed later.
