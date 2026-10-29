# Packaged desktop window handoff

The splash window remains alive while the backend starts and saved window state
is read. Create the workspace window before closing the splash. Closing the
splash must never emit a last-window event that quits a successful startup.
Closing the actual final workspace window still stops the managed backend.

Regression verification imports the real main-process entry with a simulated
Electron window lifecycle and a deliberately delayed profile read. It must not
quit during that delay or during the splash-to-workspace handoff.
