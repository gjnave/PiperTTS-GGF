# PiperTTS-GGF

This repository contains the custom Piper manager code that sits on top of the upstream `piper1-gpl` project.

## What lives here

- `payload/manager_app/main.py`
  - The GUI manager, model browser, downloader, synthesis UI, and embedded local API server support.
- `payload/manager_app/server_only.py`
  - The terminal-only API server launcher that runs without the GUI.

## What does not live here

- The upstream Piper source tree
- The wrapper `install.bat`
- The wrapper `run-piper1-manager.bat`
- The wrapper `run-piper1-api-server.bat`

Those wrapper files are meant to ship in a small release ZIP. The installer can then clone:

1. `OHF-Voice/piper1-gpl`
2. this repository

After that, it can copy `payload/manager_app` into the cloned Piper repo and run everything from there.

## Current payload path

The files in `payload/manager_app` are the app-owned files that should be treated as the reusable source of truth for the manager.
