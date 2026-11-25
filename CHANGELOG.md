# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is the one in `blender_manifest.toml`; each release is tagged
`vX.Y.Z` and published on the GitHub Releases page.

## [Unreleased]

## [0.4.1] - Unreleased

Release repair in preparation. Split distribution is not published yet.

### Fixed

- Restore the prepared distribution CPU checks and split-package CI artifacts omitted during 0.4.0 public export.
- Check required release steps and all three package roles; block publication while distribution connection and review requirements remain incomplete.


## [0.4.0] - 2026-11-25

Alpha release. Features and workflows are still in development.

### Terminology

- 「ガイドに戻す」（Back to Guide）→「配置した形に戻す」（Back to Placed Shape）。
- 「置いた形に戻す」（Back to Placed）→「調整した形に戻す」（Back to Adjusted Shape）。
- The terms distinguish the shape made by Place from the adjusted preparation shape. Operator IDs, saved data, and reset behavior are unchanged.

### Added

- **Simulate keeps its sewing frames on the timeline.** With Keep Cache on, a Live run keeps the frames from its start
  until shortly after gravity comes on, so the moment the pieces are sewn can be played back. A new switch, Keep All
  Simulate Frames (Preferences and the run settings, off by default), keeps every frame up to 1500.
- **Every run names the add-on build that made it.** The run log's first line ends with the version and the commit
  (for example `add-on 0.4.0 (1a2b3c4)`), and the run folder's `job.json` has them under `addon`. A run folder or
  .blend sent with a problem report is then read against the code that made it.
- A start carried from the last result onto a new mesh (another Particle Distance) that has triangles squashed flat
  is refused with its own way out: set Particle Distance back, or Reset the piece. It said to place the piece again,
  which does not change a carried start.
- **Space starts and stops Simulate in the work screen.** In the AC9 Cloth Sim workspace the key that plays the
  animation starts Simulate and stops it, wherever the mouse is (Shift+Space for those whose Space opens the tools
  or the search). Elsewhere it plays as before. The add-on's preferences have a Hotkeys section where the key is
  switched off or changed in place (Simulate (Start / Stop)).
- **A seam's fold can change while Simulate runs.** For a seam that was folded when the run began, its Fold Angle
  and Mountain / Valley take effect at once (eased over 30 frames), as on internal lines. Folding a seam that started
  at 0, and Turns, still take effect at the next run; those fields stay grayed while Live runs.
- **The .blend holds the whole result.** A dressed garment keeps its last run's settings and the pattern it was
  dressed with in the .blend itself. If the run folder (`AC9_ClothSim_jobs`) is deleted or the .blend is copied
  alone, Simulate and Continue still go on with the previous settings; only the frames on the timeline are lost.
- **Old runs are cleaned up.** Each garment keeps only its latest run folder; older ones are deleted after a run, a
  pattern edit carried to the 3D, and a save. Only folders this .blend wrote are deleted, never one another object
  or another .blend still uses. **Keep Old Runs** in Preferences (off by default) keeps them all.
  **AC9 Run > Settings** shows how much disk the run folders take, with a **Clean Up** button.
- **Fast Simulation Start** in Preferences, on by default: one switch for everything that makes a run start sooner.
  The solver is started ahead and waits, keeps the garment of the last Simulate, and after a pattern or
  particle-distance edit on a dressed garment, once the drafting has been still for a moment, builds the next Live
  Sim's model in the background, so the press starts much sooner. Nothing is written into the scene; while it builds
  the interface pauses briefly. It uses more GPU memory: turn it off when the GPU runs short (every run then starts
  the solver anew). It replaces **Keep the Solver Ready**, **Keep the Live Model** and **Build Ahead**, which each
  needed the one before; a choice made in those three is not carried over.
- **Accurate (Research)** is available in **AC9 Run** with **Experimental Features** enabled, for Simulate.
  It is hidden in the normal UI and off by default; switching it
  off restores the previous Native setting. This restores the research solver, without changing its physics or
  promising better drape or speed. Static bodies only; Phase and Local Sim keep their existing solver.

### Changed

- Show the internal fold's Layers controls and one-layer notes only with Experimental Features enabled; retain saved layer settings.
- Diagnose now checks displayed placed and dressed pieces together, including mixed states,
  without preparing or changing their shapes. Results include intrusion, cloth crossings and
  their partners, principal stretch, and start-shape warnings. Changed inputs invalidate old marks.
- Diagnosis has its own panel. Its display selector is shared with Overlays and chooses None,
  Problem Areas, Stretch, or Face Side. Placement no longer adds automatic quality warnings or red marks.
- **The garment stays still while a re-placed piece is sewn on.** When one piece (a collar, a band) is placed again on a
  garment already simulated and Simulate runs, the sewn pieces are frozen until the piece is sewn on, then let go; they
  are tinted like Freeze meanwhile and a line under Run says so. If the piece cannot reach its partner, they are let go
  once its stitches stop closing. The new switch Freeze Garment While Sewing (run settings, on by default) turns it off.
  Before, the sewn pieces were made heavier instead, and the garment still rose and wrinkled.
- **Live Sim is now called Simulate** (Japanese: シミュレート), on the Run button, in the messages, the play key's
  row in the preferences and the manuals. It works as before. Phase Sim keeps its name (Experimental Features).
- **Strengthen keeps the shape a hanging piece has.** Strengthen put on a piece that is already sewn and hanging
  (during Simulate, for example) makes it stiffer around the shape it has at that moment: it no longer pulls the piece
  back to the flat pattern's size or opens its folds, and the seams around it are not pulled. On a test T-shirt with
  both sleeves strengthened x20 the sleeves changed by p90 0.03 / 1.1 mm (was 5.4 mm) and the body next to them by
  p90 2 mm (was 10 mm); the rest of that movement comes from the Native 3 solver path (0.2 mm without it).
  A changed factor keeps the same shape; switching Strengthen off gives the pattern back. A piece strengthened from the
  start keeps the shape it has once sewn. An angle you set still wins: a folded seam keeps its angle, a fold line
  changed during Simulate follows the new angle, and an elastic's shrink acts as before.
- **Placed pieces stay where they are placed**: wrapping a piece round its guide no longer pushes it out of the body,
  bands included. Vertices that start inside the body are marked red with one line in the report; move them out by hand.
  **Stretch** on AC9 Piece no longer has an Auto switch: unset is x1.00 (the band's own length).
- **A seam's fold** is now an angle and a direction, **Mountain** or **Valley**, seen from the face of the piece that
  turns (**Turns**), the same as an internal line's fold; it is held as firmly as a fold line. A seam fold set in an
  older file may have been measured from the other piece and fold the other way: check its direction.
- **Ends** in **AC9 Piece** says how a placed piece goes round its guide: **Open**, **Closed** (its two ends meet,
  stretched round if it has to be) or **Left on Top** / **Right on Top** (that end, along the pattern's width, laps
  over the other). **Overlap** under it comes from the seam or the button joining the ends (**Auto**: two lines,
  or one end sewn onto a line across the other end, as a waistband over its button stand) or is typed;
  longer than once round wraps it twice at most. **Ring Size** is now **Stretch (x)**, shown for a closed piece only.
  A mirrored pair follows with left and right swapped. The garment's **Front Over** and a seam's **Over** are
  removed: a file that used them, or a role, is placed as before until you choose its Ends.
- Simulate, and Simulate after a pattern edit, start sooner: the solver's compute programs were built again after
  every pattern edit, and now come from the cache. Building the solver's model also does less repeated work
  (copies and checks that were made on every build). The computation is the same.
- Simulate after a pattern edit starts sooner again: the model build skips a coloring step the solver replaces anyway,
  makes its collision programs once per session and plans its three batches faster, with the same results. With
  **Fast Simulation Start** on, the interface pauses for a shorter moment while it prepares (only the pieces you changed are
  triangulated again), and a press drops a build that was about to be sent.
- **Fast Simulation Start** now also keeps garments with fold lines and End Taper (before, they were built from scratch
  on every Simulate). On a 9-fold-line shirt the fold angles and crossings stay within the run-to-run spread of a fresh
  build; options that read the start shape to set their folds are still built from scratch. Changing a fold angle
  builds anew.
- After a pattern edit or a new particle distance, the changed pieces' mesh is made in the background while you keep drafting, so pressing Simulate or Phase Sim afterwards no longer waits for it. A press that still has work to do shows "Preparing the garment..." at once.
- The pieces' mesh cache stays between Blender sessions when no working folder is set in the Preferences (it was kept in Blender's temporary folder and lost on closing).
- **Simulate keeps its working files on a local folder** (the add-on preferences, or Blender's temporary folder when
  left empty) and copies the finished job next to the .blend afterwards, so a slow drive no longer holds up Start
  and Stop. A test option can keep the built garment between runs so that pressing Start again after moving a piece
  or changing a fabric value starts sooner (off by default).
- **Faster start on Windows CPUs with fast and efficient cores**: the solver process no longer gets throttled as
  background work and prefers the fast cores (only when the CPU has at least 8 of them, or they are half the CPU).
  Simulate starts noticeably sooner on the one CPU measured so far (16 fast + 12 efficient logical processors);
  other CPUs are not measured yet.
- **Simulate** from placed pieces sews them without gravity first: once the seams are closed (the widest gap under
  3 mm, or no longer closing), the garment is held still for a moment and then the gravity comes on; the fabric's
  bending hysteresis waits until the garment is quiet. A long garment no longer swings up after the sewing, and the
  result is the same from run to run. A Live going on from a result whose seams are closed starts with the gravity at
  once; one whose seams stay a few millimeters open counts them as closed after half a second. Accurate (Research)
  is unchanged.
- **Simulate** with a piece put back to placed on a garment already sewn (a collar, a pocket): only that piece is sewn
  without gravity; the rest of the garment keeps its gravity and, while the piece is being sewn on, weighs more, so the
  seams bring the piece to the garment instead of lifting the garment toward it (a jacket's front rose 27 mm with a
  collar put back; now 1 mm). The garment is not held still, so a neckline that had gone wrong can still follow the
  piece. Pieces placed apart are sewn as before.

### Removed

- **Local Sim** is gone from the screen (the Run button, its Gravity, the right-click row and the button under a
  Fold field). The selected pieces alone, without gravity, drifted. To run part of a garment, switch the other
  pieces **Off** or **Freeze** them and press **Simulate**.
- **Piece roles** (**Role**, **Start Shape**, the **Piece Roles** panel) are shown only with **Experimental
  Features**. Simulate does not read them; what it does is set by the fields of **AC9 Piece** such as **Ends**.
  Roles already set are kept in the file.
- The **Native 3** switch (under **Experimental Features**): the faster Simulate is always used, and a garment it
  does not take runs the usual Live and says so in one line, as before. A file saved with the switch off opens with it
  on. The messages that named it now say "the usual calculation".

### Fixed

- Give Add Seam and Add Seam (M:N) shared One Edge / Two Points controls in the Tool Header. Keep Ctrl for temporary snap inversion, preserve authored internal corners through mirrors, and store closed internal seam intervals consistently across selection, editing, and meshing.
- Recover legacy garment ownership only from a unique saved pattern and matching mesh layout. Preserve ambiguous results and show an ownership warning with an explicit recheck action.
- Restore duplicate and missing flat-pattern displays through the existing Restore Pieces action, keeping retired objects in a hidden recovery collection and supporting Undo/Redo.
- Keep 3D seams, notches and internal lines on the displayed cloth after moving or rotating its 2D pattern, before running simulation again. Refresh edited sewing targets and interpolation when triangle connections change.
- Detect duplicate, missing, invalid and foreign flat-pattern objects before edits. Refuse ambiguous updates without deleting user copies, preserve inspection selection and explicit JSON reload, and recheck after load, undo and redo.
- Update the bundled avatar's neutral body shape while preserving its rig, vertex count and pose controls.
- Synchronize new mirror-paired internal lines and persist their authored correspondence for movement, curve editing, deletion, folds and elastic settings; keep placement unlink separate from pattern symmetry.
- Remove preparation-only missing fold synthesis. Preserve authored asymmetry with separate meshes, and refuse unmatched folds on old shared shapes instead of folding an invisible counterpart.
- Apply placed internal folds from either side of a shared mirror pair even when the other pattern has no matching line, without changing the authored pattern.
- Fold floor placements using the fabric face and current crease, including tilted and inverted planes.
- Apply and reset self-mirrored crease pairs in a consistent order from either selected line.
- Keep private rollback copies out of generated-object readers; release each copy once and preserve foreign document auxiliaries.
- Numeric Input reaches the requested outline/line distance during symmetric editing and shows fractional distances in the selected length unit.
- Clicking empty space in an AC9 tool clears the synchronized red dots in both 2D and 3D.
- Garments with the same label keep separate meshes, placed shapes and eye states by document.
- A partial Back to Placed selects and edits the same shape that is displayed.
- Place uses visible pattern faces to keep pieces off the pattern, so distant or hidden patterns no longer block
  empty ground. Coplanar floors keep the same tolerance, and guide hover and placement share visibility checks.
- Starting a simulation from placed pieces keeps the garment mesh visible while it runs.
  The display mask is reconciled when the placed objects are released, preserving Off and hidden pieces.
- 3D movement follows the editing basis of each piece: linked placements move and rotate as a mirrored pair;
  accepted simulation results can be corrected on one side. Local Sim results follow the same rule.
- Place draws and commits the same final mesh candidate. Replacing a piece on a cylinder clears free movement
  and rotation while keeping its placement tilt and fold settings. Transform gizmos appear with Whole Pieces.
- Back to Guide clears free placement offsets. Back to Placed restores pre-simulation placement adjustments.
  Saved shapes whose origin cannot be identified require one of these explicit resets before free transforms.
- **Undo after a refused or canceled operation no longer crashes.** Part to Seam when it refused, and Band on
  Edge, Clear Placement and Move Outside when they were canceled, put the placed pieces back without an undo step;
  the next Ctrl+Z could crash Blender, and after a refused Part to Seam the pieces set back to placed also showed
  twice. Such an operation now leaves one step in the undo history ("AC9 Cloth Sim: canceled") when it changed
  something; one that touched nothing leaves none.
- Ctrl+Z and Ctrl+Shift+Z are not taken while Simulate runs: stop it with Esc first (a line says so).
- Deleting a seam, an internal line or a measurement with X / Del is its own undo step; one Ctrl+Z went back past
  it and the deleted thing stayed deleted.
- From Mesh refuses a pattern piece, a placed piece or the garment as its source (with the Mesh field empty and one
  of them active it failed half way, the pattern's objects gone).
- **A run starts from the garment as shown.** A .blend saved while a run was going could keep a garment whose saved
  run described another shape: the next Simulate then started some pieces from the placement rule instead of where
  they were shown (a collar jumped from the back of the neck to the front), and the 3D internal lines and notches were
  drawn through other pieces (long blue lines that went away after a stop and a restart). The run now goes on from the
  garment as shown and says so; AC9 Run shows a red box with **Use the Shown Garment** before the press.
- A piece whose shape on screen could not be compared with its start is now said and shown red, instead of passing
  in silence. **Diagnose** on the Placement panel lists what may make the next run start other than as shown,
  with what to do for each.
- The first Simulate after installing or updating the add-on no longer says the preparation has not moved for 30
  seconds while it builds its GPU parts (it can take a while the first time); a stop pressed meanwhile ends the run
  when that step is done instead of ending the waiting solver.
- A Simulate interrupted while it wrote its grab or settings file no longer leaves its working folder behind: the
  leftover file made the folder look foreign, so it was neither cleaned up nor copied next to the .blend.
- **Fewer solver crashes, and clearer ones.** A seam hinge whose points are not all different (it could come from a
  piece switched off next to a seam) is no longer made: it could crash the solver process before the first frame. The
  cloth is checked before it reaches the solver (invalid positions, triangles, hinges or stitches), and the run stops
  with a reason instead of crashing. When the solver process still ends abnormally, the message says so with its code
  and suggests pressing again; a stage that stops for a reason shows that reason. The run folder keeps where it
  stopped, for the developers.
- **Simulate** on a garment with more points than it can take (120,000) stops before running and asks for a larger
  Particle Distance; before, it failed at stage 1 every time. A garment whose seams got no stitch point runs the usual
  Live instead of failing.
- A run that fails because the GPU ran out of memory says so, and what to turn off (**Fast Simulation Start**) or to make Particle Distance larger. Before, it said only that stage 1 failed.
- A run that stops moving before its first frame is shown as possibly stuck, and **Stop** ends it after 30 seconds
  without any change instead of waiting a full minute. A first run that is still building its compute programs is
  not mistaken for a stuck one.
- **Place** works before **Detect Guides** has been run: a piece laid flat on a face no longer stops with an error
  about missing guides.
- The first placement of a piece no longer pauses for over a second while the seam marks are carried onto it.
- A pair meshed differently (shown as two objects, one following the other) no longer opens up on one side: centering
  the leader's origin moved the follower off its shape by the mirrored amount (about 66 mm on a test pair of
  trousers), and the follower then counted as moved by hand, so placing again kept it there. A file saved like this
  is put right by placing the shifted piece again.
- Seams on soft fabrics close as fast as on any other fabric. While sewing without gravity (the start of Simulate,
  the no-gravity stages) each stitch's pull is capped, and the cap came from the garment's default fabric: a tenth of
  the usual for Cotton Jersey (a new garment's default) and far less for Rib Knit, even when the pieces had fabrics of
  their own. Far seams such as the shoulders then closed very slowly or not before the gravity came on, and the
  garment lost its shape. The cap is now the same for every fabric (the value the woven fabrics had, so their
  garments run as before). On a test coat in jersey: shoulder still 31 mm open after 600 frames -> closed in 65
  frames, cloth crossings 16 -> 0.
- A piece placed round the torso is no longer pushed out past the arms when no piece is round an arm yet. The arms
  are left out of the body while the body is sewn, but the placement kept clear of them in that case, so a back side
  panel placed between the torso and a hanging arm started outside the forearm (and moved back once a sleeve was
  placed). Garments with sleeves start as before.
- Continue and Live after many pattern edits carried to the 3D in a row (more than ten without simulating) take
  over the last run's settings again; they had started with the default ones, and a model built ahead was not
  used.
- With the solver kept ready, a later Simulate could stop with "stage 1 failed" (seen after turning a piece Off).
- A piece moved by hand and then made larger in the pattern started with the added cloth pressed onto the edge of
  its old shape, and the run stopped with "stage 1 failed". The added cloth now carries on from the placed shape, as
  the placed piece on screen shows it. A start with triangles squashed flat is refused before the run, naming the
  pieces (it stopped with "stage 1 failed").

## [0.3.0] - 2026-11-20

Alpha release. Features and workflows are still in development.

### Added

- **Face Side** under **Overlays > Diagnose** colors the front of the cloth blue and the back red, including
  pieces with display thickness. It is off by default.
- Display thickness is shown on placed pieces as well as on the dressed garment, using the same **Thickness**
  setting. This changes the display only.
- **Layer has no effect** is said before a run: a piece whose Layer reaches under half of its vertices (they lie
  next to a seam joining it to another layer, where the usual contact holds) gets a warning, a line in the log and red
  in **Problems**. A narrow placket sewn along its edge to a front on another layer lost all 242 of its vertices this
  way, silently. Garments with a fold solved as one layer decide by seam pairs instead, and nothing is said for them.
- **Seams from 3D Drape (FBX)** in **Setup**, and a **3D Drape (FBX)** field when you **Import BOM** (empty: the one
  FBX in the BOM's folder). The garment's FBX exported with the BOM decides which pieces each BOM seam sews, from where
  the pieces meet in 3D, and which pieces are one garment. It is for BOMs whose numbers several pieces share (a
  project that gathers several garments, or parts brought in from other files): on a 21-piece jacket the BOM alone
  found the right partner for 70 of 140 seams, the FBX for 138. Pieces not in the FBX keep the BOM's own rules.
- **Accurate (Research)** is available in **AC9 Run** with **Experimental Features** enabled, for Live Sim.
  It is hidden in the normal UI and off by default; switching it
  off restores the previous Native setting. This restores the research solver, without changing its physics or
  promising better drape or speed. Static bodies only; Phase and Local Sim keep their existing solver.
- When the NVIDIA driver is missing or older than the add-on needs, a warning says so as a run starts (the run
  goes on). The README lists the supported GPUs: GeForce RTX 20 to 50 series.
- Fabrics **Denim** (measured values), **Satin**, **Chiffon**, **Nylon Taffeta** and **French Terry** (estimates; the
  manual's fabric page lists which presets are measured).

### Changed

- **Ring Size (x)** and **Auto** are shown for every piece, regardless of its role. They can be edited after
  placement on a curved guide; they remain visible but disabled for unplaced or flat pieces. Manual Ring Size
  uses the piece's own length for ring placement, while **Auto** restores the existing automatic placement.
  The existing collar-stand placement is shared with cuffs and other pieces, without assigning them a role.
- **Live Sim** from placed pieces sews them without gravity first: once the seams are closed (the widest gap under
  3 mm, or no longer closing), the garment is held still for a moment and then the gravity comes on; the fabric's
  bending hysteresis waits until the garment is quiet. A long garment no longer swings up after the sewing, and the
  motion from sewing is cleared before gravity starts. Continuing from a result whose seams are closed starts
  with gravity at once; one whose seams stay a few millimeters open counts them as closed after half a second. Accurate (Research)
  is unchanged.
- **Live Sim** with a piece put back to placed on a garment already sewn (a collar, a pocket): only that piece is sewn
  without gravity; the rest of the garment keeps its gravity and, while the piece is being sewn on, weighs more, so the
  seams bring the piece to the garment instead of lifting the garment toward it (a jacket's front rose 27 mm with a
  collar put back; now 1 mm). The garment is not held still, so a neckline that had gone wrong can still follow the
  piece. Pieces placed apart are sewn as before.
- Fold **Turns** and **Layers**: on a mirror pair (a **Mirror Copy**, the two lines of a piece with **Symmetric
  Edit**, a BOM's left and right pieces) A names the same side on both lines -- the mirror image -- and a fold is
  copied to the mirror line with the same letters (before, the copy showed the other letter for the same side). Files
  from earlier versions open unchanged; a line whose mirror is named the other way says so, with **Match Mirror A/B**.
- **AC9 Run** uses less space in the Tool tab: the run button, gravity, the 3D update controls and a short status
  remain visible; the other settings and full log are under the collapsed **Settings** section.
- **Numeric Input...** opens a dialog for numeric pattern edits from the right-click menu or Tool tab. It works
  on corners, internal-line points and multiple selected elements across pieces. Whole pieces and closed internal
  shapes also have width and height fields. These operations no longer depend on the Redo panel (F9).
- **Whole Pieces** selects placed pieces and pieces on the dressed garment in the 3D view, with a move or rotate
  gizmo. The header's **Gizmo** setting or **W** switches between them. Selected pieces have a tint and outline in
  both views, using Blender's selection colors. Blender's standard selection tools select ordinary objects, not pieces.
- **Move by Hand** has been removed: use **Whole Pieces** and its gizmo to move or rotate placed pieces. Running
  **Detect Guides** again now recreates missing placed shapes and preserves their recorded hand adjustments;
  a development fixture restored all 13 shapes, with a maximum position difference of 0.00 mm from the previous path.
- **Place**: a click only selects (on nothing it deselects); only a drag places. A click on the body, a cylinder or
  the ground no longer places the selected pieces there, and a click on the dressed garment picks its piece even
  when pieces are selected. A drag from the dressed garment places the selected pieces as a drag from the body does
  (it no longer moves the garment piece under it); pieces being worn are not placed again (a line says to use
  **Back to Placed** first). A placed piece slides around its cylinder wherever it is held. To box select pieces,
  use **Whole Pieces**: a drag from nothing in 3D picks the dressed garment's pieces one by one.
- The piece states are named **Active**, **Freeze**, **Inactive** (stays, nothing touches it, its seams still
  pull) and **Off** (stays, out of the simulation with its seams) everywhere; the same state had two names before.
  Files made before open as they were.
- The right-click menu of a placed piece opens in every AC9 tool (it was there with Place alone, and with the test
  features off it drew empty). Placed and dressed pieces get Move, Rotate, a State submenu and Hide; a dressed
  piece also gets Back to Guide, Back to Flat, Mirror Placement and Swap Sides.
- A placed piece's origin is on the piece as soon as it is placed, so a placed piece clicked and turned with **R**
  turns about its own middle without pressing **Move by Hand** first (it was up to 34 mm off the piece).
- Placing a dressed piece again no longer stops with an error that left the 3D view unresponsive; Ctrl+Z takes a
  placement back in one step.
- The tooltips of **Edit Fabric** and **Edit Interlining** say in plain words what raising or lowering each value
  does, with the presets as a scale, and the manual's fabric page has a table of what each number means.
- In the Japanese interface the fabric and interlining presets are all named in Japanese (before, two took
  Blender's own words, one of them the wrong word for leather, and the rest stayed English). The saved name stays
  English, so a garment opens the same in any language.
- Native 3 limits the search used to prepare a simulation. The compact and reduction search budgets are now
  2,000 steps instead of up to 20,000; some inputs can use a different preparation plan. The independent checks
  of a plan are unchanged.
- Curve handles of the flat pattern are shown at the picked corners and at both ends of the picked edges,
  whichever piece is under the cursor (a handle sticking out of the piece went away as the mouse went to it).
  Only those handles can be grabbed.

### Fixed

- After a simulation, **Back to Placed** restores the shape used to start that simulation, including **Part to Seam**
  and subsequent hand adjustments. It no longer replaces that shape with the one from before **Part to Seam**
  (a development fixture's position error fell from 107.6 mm to 0.0 mm).
- **Part to Seam** preserves a piece's placed orientation when its seam points lie on a straight line, rather than
  choosing an arbitrary rotation around the seam.
- A click that only selects a piece no longer adds an undo step. The red click mark is shown in both views;
  in 3D its appearance distinguishes a visible surface, the back of the cloth and a point hidden behind cloth.
- **Work Screen** separates the pattern and 3D views even when objects are directly under **Scene Collection**.
  Those objects are moved into **Scene Objects** when the work screen is set up.
- **Properties > Tool** follows the last explicit interaction for pieces, seams, fabrics, interlinings, buttons,
  measurements, internal lines and outline elements. Updating a list or synchronizing a selection between views
  no longer replaces those details. Clicking an already selected seam row while another detail is shown first
  shows that seam; a further click while showing it still deselects it.
- **Garment from Selection** and **Update BOM Seams** no longer stop with an error (`KeyError`) after one piece of a
  BOM's left / right pair was made the mirror image of the other (by Blend, Symmetrize, or switching a piece back on
  from Off). The mirror copy kept the other piece's line numbers, which the BOM does not use for that piece; it now
  keeps the piece's own. A BOM seam whose line is no longer on its piece is left out and counted in the report.
- With no NVIDIA GPU the add-on can use (no driver, or one too old), a simulation no longer runs on the CPU for
  many minutes without a word: it stops at once and asks to install or update the NVIDIA driver.
- Installing a new version over one that is loaded (without restarting Blender) no longer stops with
  "module ... has no attribute": every module of the add-on is reloaded, not only a listed few. F3 (Reload
  Scripts) reloads them all too.
- A pair put back with **Back to Placed** starts its mirrored side from the shape shown: it was made a few
  millimeters off and put right at the start with a warning in the log.
- With the solver kept ready, a later Live Sim could stop with "stage 1 failed" (seen after turning a piece Off).
- A piece moved by hand and then made larger in the pattern started with the added cloth pressed onto the edge of
  its old shape, and the run stopped with "stage 1 failed". The added cloth now carries on from the placed shape, as
  the placed piece on screen shows it. A start with triangles squashed flat is refused before the run, naming the
  pieces (it stopped with "stage 1 failed").

## [0.2.0] - 2026-11-13

Still an alpha. **Live Sim** is now the one run button: it dresses from the placed pieces and goes on from the
dressed shape until you stop it. Features still being tried are behind **Experimental Features** in the add-on's
preferences (off by default).

### Added

- **Add Avatar** in Setup adds the add-on's avatar (CC0, `avatar.blend`) to the scene and sets it as the Body when
  none is set.
- **Experimental Features** in the add-on's preferences: shows Phase Sim, Local Sim, the Native 3 switch, how a seam
  is pulled and the pieces' Layer.
- Edit Pieces: grabbing an edge between corners picked with the box moves everything picked.
- **Smooth Corner / Sharp Corner** (right-click on a corner of the flat pattern or on one of its curve handles): the
  handles either side of a smooth corner stay on one line, so the outline no longer kinks there as it is adjusted.
  On a symmetric piece's center line the other handle is the dragged one's mirror image.
- The avatar follows its rig (Armature modifiers on the body and the eyes), so it can be posed before dressing. It
  has a plain mannequin material.
- While the pieces are edited symmetrically, editing a seam (flip, on / off, confirm, delete, how it is pulled,
  moving an end) does the same to its mirror image.
- A run says "Preparing the simulation (N s elapsed)" in the 3D view until the first frame. The first run on a PC
  (and the first after an update) says it is building the compute programs for this PC; later runs start quickly.

### Changed

- A run starts each placed piece from the shape seen on screen (the mirrored side of a pair made by **Mirror Copy**
  could start far from where it was shown after a Reset). Pieces placed and not moved by hand are placed again on
  the body as it is when a run is pressed (after a sculpt, an edit or a pose) and on an edited pattern.
- **3D Follows Edits** is off by default: pattern edits go to the 3D with **Update 3D** or the next run.
- **Duplicate** and **Mirror Copy** give the copy its own shown name, numbered as Blender does (.001).
- In the seam list, clicking the selected row again deselects the seam; clicking on nothing in any tool does too.
- Work Screen: the 3D views are lit so the body and the legs are no longer dark gray, and the add-on's text in the
  3D view sits on a dark band so it reads over the white cloth.
- On Edge text says the band is placed as a ring and Live Sim pulls it onto the sewn edge.
- The user guide's first garment chapter starts from drawing a pattern, with screenshots (Japanese and English). The
  README links to the documentation site.
- Phase Sim and Local Sim are hidden unless Experimental Features is on; the screen text and the user guide say
  Live Sim. Fast Live is no longer shown or used (a file saved with it on opens with it off).
- Native 3 is on by default; a garment it does not take (a piece on a Layer above 0) runs the usual Live and says
  so in one line.
- A new garment's default fabric is **Cotton Jersey** (it was Cotton Poplin). A saved garment keeps its fabrics.
- Number fields in dialogs drag in smaller steps (lengths 1 mm, angles 0.5 deg a pixel).
- Guide text in the 3D view starts to the right of the toolbar; the Type Move button left the Placement panel (the
  placed piece's location and rotation are in AC9 Piece).
- Japanese for the reasons Live starts again from the shown shape.

### Fixed

- Some NVIDIA GPUs (seen on an RTX 30 series laptop) stopped at stage 1 with "stage 1 failed": one solver kernel used
  more GPU shared memory than a kernel may. It now fits on RTX 20 to 50 series GPUs. When the GPU cannot prepare the
  simulation, the message says whether a driver update helps (a driver older than the CUDA the add-on uses) or asks
  for the job folder.
- A front / back pair follows a corner or internal-line move in the pattern mirrored correctly (it shrank when the
  other grew).
- The solver's preload bar no longer stays on screen while a run goes.

## [0.1.1] - 2026-11-12

Still an alpha: a .blend saved with this version may not open in an earlier one (pieces laid flat, pairs with
a front / back mirror plane).

The ZIP no longer bundles SciPy: the simulation's Python packages are newton 1.6.0 and warp-lang 1.17.0 (Windows
x64), and the ZIP is about 37 MB smaller.

### Added

- **Strengthen** in AC9 Piece: multiplies the stretch, shear and bending stiffness of the selected pieces by a
  factor (1-100, 5 by default). It stays on the result, can be switched or changed during Live Sim, and is
  shown with a purple tint. It is separate from Interlining.
- Bags and other items without a body: **Place** off the guides lays pieces flat on the face under the cursor,
  or on the ground (Z = 0); Phase Sim and Live Sim run without a body when the pieces are laid flat.
- **Floor (Z = 0)** in Run: a floor the cloth lands on, nothing added to the scene.
- Fabrics **Leather** and **Synthetic Leather** (measured values) and **Canvas** (bending estimated).
- **Units** in Setup: one length unit (mm, cm, m, inch) for every shown or typed length, including the
  measurements. Length fields accept a unit ("5cm"). Particle Distance stays in mm.
- Pairs can mirror **front to back** as well as left to right: **Mirror Copy**'s F9 has **Mirror Plane**, and it
  can be changed later. A front / back pair laid flat mirrors about its own plane.
- AC9 Piece has a **Pair** box (partner, linked or not, mirror plane, and every pair button), a placed piece's
  location and rotation (folded), and a flat piece's **Orientation** on the face it lies on.
- Pattern editing: **Move by Number**, a dotted guide with Horizontal / Vertical / 45 deg while Shift is held,
  internal lines drawn with any tool, notches snapping to an edge's middle and to the partner's notch.

### Changed

- The solver no longer uses SciPy: point queries, mesh diagnostics and the local contact repair use NumPy and
  Warp CPU helpers instead.
- Pattern editing: the right-click menu holds every action, and keys are shortcuts to it. S is Scale only;
  Ctrl turns snapping off and Shift holds the angle everywhere. Parallel Line and Slash and Spread ask for their
  numbers first.
- **Part to Seam** and **Band on Edge** ask for their targets: press the button, then click the targets
  (Shift adds or removes, A all, Enter applies, Esc cancels). The choice lasts for this operation only.
- **Back to Flat** lays pieces flat where they are (their place and turn kept) instead of taking them out of 3D.
- **Mirror 3D to Pair** moved to AC9 Piece and is pressable only when both sides are on the simulated garment.
- Messages and seam lengths are shown in the scene's unit.
- Measure takes everything again when the body is reshaped, scaled or turned; tapes laid by hand follow the skin.

### Fixed

- Moving or turning a whole piece in the pattern kept its placed shape's points where the piece had been.
- With Place, clicking a selected placed piece without moving the mouse placed it again (a stood-up piece could
  slide away); it now only selects it.
- A turn on the pattern no longer turns the placed piece.
- Seams of a front / back Mirror Copy landed on the original's pieces.
- Deleting a piece left its placed object behind (a later Mirror Copy under the same name took it over).
- The Redo of Add Dart failed; Ctrl+Z after a right-click menu edit left the pattern edited.
- Live Sim / Phase Sim stopped before starting when every piece of the last result was Off and the pieces On
  had never been simulated.
- Folded layers and seam fitting: folds marked No Side, and sewn targets across overlapping folded layers.
- With the Japanese interface, some tooltips (such as Duplicate), the piece states' and seam path's items and
  several messages were shown in English.
- The solver rejects non-finite values before they reach the cloth, and handles a piece without spring
  constraints.

## [0.1.0] - 2026-11-05

First public release. Features are missing, the screens and steps will change, and a .blend saved with
this version may not open in the next one. Windows and an NVIDIA GPU, Blender 5.2.

The ZIP bundles the simulation's Python packages unmodified from PyPI: newton 1.6.0, warp-lang 1.17.0 and
scipy 1.18.1 (Windows x64). Their licenses are in `THIRD_PARTY_NOTICES.txt` and `licenses/`.

[Unreleased]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/openfashion-labs/ac9_cloth_sim/releases/tag/v0.1.0
