<h1 align="center">GET GOING FAST - PiperTTS GUI & Server</h1>
<div align="center" style="display: flex; justify-content: center; margin-top: 10px;">
  <a href="https://getgoingfast.pro"><img src="https://www.cognibuild.ai/wp-content/uploads/2024/09/new.webp" style="margin-right: 5px;"></a>
</div>
<div align="center" style="margin-top: 8px;">
  <a href="https://www.patreon.com/posts/157110693">Buy the Quick Installer / Join as a Member / Get Community Assistance</a>
</div>
<br/>
<br/> 

# PiperTTS-GGF

`PiperTTS-GGF` is a runnable Piper repository that includes:

- the upstream `piper1-gpl` source tree
- the custom `manager_app` GUI
- the terminal-only local API server launcher in `manager_app/server_only.py`

## Main app files

- `manager_app/main.py`
- `manager_app/server_only.py`

## Install model

This repository is intended to be cloned directly by the public wrapper installer.

The wrapper:

1. clones `PiperTTS-GGF`
2. creates `.venv` inside this repo
3. installs Piper from this repo
4. runs the GUI or API server from this repo

There is no separate payload copy step anymore.

## Branding

The GUI is branded for:

- `GetGoingFast.pro`
- `https://www.youtube.com/@cognibuild`
