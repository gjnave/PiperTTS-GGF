<h1 align="center">GET GOING FAST - PiperTTS GUI & Server</h1>
<div align="center" style="display: flex; justify-content: center; margin-top: 10px;">
  <a href="https://getgoingfast.pro"><img src="https://www.cognibuild.ai/wp-content/uploads/2024/09/new.webp" style="margin-right: 5px;"></a>
</div>
<div align="center" style="margin-top: 8px;">
  <a href="https://www.patreon.com/posts/157110693">Buy the Quick Installer / Join as a Member / Get Community Assistance</a>
</div>
<br/>
<br/> 
Manual Installation: 
<ol>
  <li>Install Python 3 if it is not already installed.</li>
  <li>Install Git for Windows if it is not already installed.</li>
  <li>Open Command Prompt in the folder where you want to install Piper TTS.</li>
  <li>Clone the PiperTTS-GGF repo:
    <pre><code>git clone https://github.com/gjnave/PiperTTS-GGF.git PiperTTS-GGF</code></pre>
  </li>
  <li>Go into the repo:
    <pre><code>cd PiperTTS-GGF</code></pre>
  </li>
  <li>Create a virtual environment:
    <pre><code>python -m venv .venv</code></pre>
  </li>
  <li>Activate the virtual environment:
    <pre><code>.venv\Scripts\activate</code></pre>
  </li>
  <li>Upgrade pip, setuptools, and wheel:
    <pre><code>python -m pip install --upgrade pip setuptools wheel</code></pre>
  </li>
  <li>Install Piper from the repo:
    <pre><code>python -m pip install .</code></pre>
  </li>
  <li>If that fails, install the official Piper wheel instead:
    <pre><code>python -m pip install --prefer-binary piper-tts==1.4.2</code></pre>
  </li>
  <li>Verify that Piper installed correctly:
    <pre><code>python -c "import piper; import onnxruntime; print('Verified Piper runtime in venv.')"</code></pre>
  </li>
  <li>Run the GUI:
    <pre><code>python manager_app\main.py</code></pre>
  </li>
  <li>Or run the local API server:
    <pre><code>python manager_app\server_only.py</code></pre>
  </li>
</ol>
----------------------------------------------<br></br>
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
