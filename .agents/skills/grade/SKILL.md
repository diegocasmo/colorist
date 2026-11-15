---
name: grade
description: Color-grade Diego's guitar performance videos (iPhone HLG footage of his usual room setup with a colored lamp on the wardrobe) with his approved, fixed look for that lamp color. Use when he shares one of these videos and asks to grade, color, edit, or "make it look good".
---

# Grade

Each lamp color (and shade, e.g. `blue` and the paler cyan `light-blue`) has one frozen look in `look.json`, applied by a script. The script picks the look automatically: the wall's dominant channel, then the look whose reference wall tint is closest. Do not invent, re-tune, or "adapt" a grade per video by eye: that is what made past results inconsistent. The only per-video change is an automatic exposure and tint match against that color's reference clip.

## Input

The iPhone original (HLG HDR), not a CapCut export. CapCut exports SDR and clips the lamp color: on the blue setup, 1.7-3.2% of pixels clipped behind his head vs 0.01-0.04% in the original. If he sends a file whose transfer isn't `arib-std-b67`, ask for the original (Photos → File → Export → Export Unmodified Original, or a clip trimmed on the iPhone).

His workflow: grade the original here → import the graded file into CapCut → sync Cubase audio, trim, export. Remind him to keep the CapCut timeline at the clip's frame rate (24fps), or CapCut adds frames.

## Run

```bash
uv run .agents/skills/grade/scripts/grade.py <video.mov> [-o out.mp4]
```

A 40s 4K clip takes about 2 minutes. Output defaults to `<name>_graded.mp4` next to the input, plus `<name>_graded_compare.jpg` (original | graded at 20%, 50%, 80%).

Then:

1. Read the printed lamp color, match gains, and preservation checks.
2. Open the comparison image and look at it yourself. Compare against `reference/approved-<color>.jpg` (original | approved pairs): face brighter than the wall, wall color rich but not neon, skin warm (not magenta), black clothes dark but textured, no blotches in the white shirt, candle still warm.
3. Report to Diego: output paths, the checks, and anything off. Keep it short.

If the script prints `WARNING ... clamped`, the clip is far from the reference (lamp shade, white balance, or exposure changed). Say so and show the comparison instead of calling it done. If it says there is no look for the lamp color, follow "New lamp color".

## Rules for any change to the pipeline

- **The HLG to SDR decode is fixed** (in `look.py`). Guessing it turned the same red wall coral or deep red.
- **Removing lamp color from the performer must keep brightness constant.** Near-white pixels have noisy hue from compression; any step that changes brightness based on hue or saturation there turns that noise into visible blotches. Skin is the same: `skin_lift` blotched the cheeks on the light-blue clip, so that look keeps it at 0.
- **Remove spill evenly.** A narrow spill window desaturates noisy-hue pixels by different amounts (grey and lilac patches on the shirt). Use `spill_flat` so the whole tint range gets the same correction.
- **Preservation is the script's job.** It is a color-only LUT (no crop, scale, sharpen, or denoise), audio is copied bit-exact, and the script exits non-zero if frame count, frame rate, displayed size, audio, or the SDR Rec.709 tags don't check out.

## Changing a look or adding a lamp color

Never save an unapproved look.

1. Measure the clip first (wall hue, skin hue, spill on the shirt, clipping) and explain what the look must fix.
2. Add or edit the entry in `look.json` → `looks` (`wall_channel` = the dominant channel: `red`, `green`, or `blue`). Render subtle/medium/bold stills of 3 frames next to the original, check them for artifacts (zoom on shirt and face), and let Diego pick.
3. Save the pick, add `reference/approved-<color>.jpg`, and run once with `--calibrate <look>` on that clip to store its reference (this is also what lets the auto-pick choose it).
4. Confirm every other look is unchanged: their LUTs (`write_cube`) must be byte-identical before and after.
