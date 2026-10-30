# Packaged desktop window handoff

The splash window remains alive while the backend starts and saved window state
is read. Create the workspace window before closing the splash. Closing the
splash must never emit a last-window event that quits a successful startup.
Closing the actual final workspace window still stops the managed backend.

Regression verification imports the real main-process entry with a simulated
Electron window lifecycle and a deliberately delayed profile read. It must not
quit during that delay or during the splash-to-workspace handoff.

## Release artifact binding

Release assembly uses a new application directory containing only the current
compiled output and runtime package metadata. Developer `desktop/out` and its
node_modules are never fallback application inputs. All third-party renderer
dependencies are bundled; main/preload may import only Electron and Node builtins.

Before making an installer, compare every compiled file byte-for-byte against
the unpacked executable's app.asar, verify its main entry and version, and record
digests. Missing or stale files block installer generation. A version label or a
successful backend probe alone is not evidence that the desktop artifact is current.

Native release smoke starts the real packaged executable in a newly owned
synthetic profile, waits for the renderer's online state, saves a main-window
capture, and exits through the normal backend shutdown path. Capture-and-quit
and require-online flags apply only to the explicit capture test mode; an early
exit without an online workspace image fails the smoke.
