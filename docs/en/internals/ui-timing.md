---
title: UI timing recorder (experimental)
---

# UI timing recorder (experimental)

In the add-on preferences, enable **Experimental Features** to reveal **Record UI Timing**. Both switches default to off. Enable recording, then work normally. To investigate the first action after startup, save preferences and restart Blender. Disabling Experimental Features also stops recording.

**Open Timing Logs** opens the local folder containing one directory per recording session. Nothing is uploaded automatically. Logs include the loaded file path.

Records cover entry into AC9 operations, callback names, wall duration, process CPU duration, the next 3D draw callback, event-loop gaps of at least 0.5 seconds, and file-load boundaries. A separate observer attempts a Python stack sample after a two-second gap. The existing solver lifecycle log location is included for correlation.

Operation starts are flushed before the callback runs, so an unfinished start can survive a process crash. Complete recovery after forced termination or OS failure is not guaranteed. Four files rotate within each session, retaining approximately 32 MiB. Earlier session directories are not deleted automatically.

Entry time is not the physical mouse timestamp. A draw receipt is a 3D draw callback, not completed monitor presentation. A stall that prevents Python from running also prevents a contemporaneous stack sample; a recovered gap or the last start record may be the only evidence. Startup before add-on registration, other add-ons' internal timings, and native-code causes are outside this recorder's attribution.

Recording observes callbacks without changing patterns, garment geometry, or solver settings. Turn it off when the investigation is finished.
