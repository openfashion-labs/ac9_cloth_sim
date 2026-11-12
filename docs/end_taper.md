# End taper for a fold line

Select an internal line that ends inside the pattern, set its Fold Angle, and
set **Taper** (a length in the scene's unit). This is the distance from each internal endpoint over which
the target angle fades smoothly to zero. Beyond that distance the full angle
applies. An endpoint on the pattern boundary keeps its full angle. A line with
two internal endpoints fades toward both ends.

The setting defaults to **0**, which keeps the previous constant-angle behavior.
Existing files are unchanged. The value is saved with the line's fold settings
and follows its mirrored twin. Closed internal loops and holes do not use taper.
There is no universal recommended distance; the tested sleeve uses 48 mm.

Taper takes effect during simulation. It does not fold a partial line in the
placed preview or replace the cloth's natural geometry with its current shape.
Move by Hand keeps the visible geometry and the line setting.

Live can update the angle or taper distance, including disabling taper, with
the existing 30-frame transition. A no-edit stop can keep its full checkpoint.
After a Live taper/angle edit, a full checkpoint is unavailable for that run;
the next start rebuilds from the displayed shape with the saved fold settings.
The displayed fold is kept, but the solver's complete internal history is not.

Taper can coexist with one-layer or bound cloth on other pieces. It rejects
taper on a piece containing bound vertices or their host triangles, welded
meshes, and overlapping fold profile/free/ramp/turn controls. A bound-cloth run
still rebuilds from the displayed shape on restart; full-state checkpoints for
binding are outside the current checkpoint contract.
