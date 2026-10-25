# Contributing

Thanks for taking a look. browser-broker now lives in
[browser-agent](https://github.com/nicedreamzapp/browser-agent/tree/main/broker), and this repo
is frozen at the last standalone version so existing installs that pull from here keep working.

## Reporting a problem

New work, issues and pull requests go to
[browser-agent](https://github.com/nicedreamzapp/browser-agent/issues/new/choose). Only open an
[issue here](https://github.com/nicedreamzapp/browser-broker/issues/new/choose) if the problem is
in this frozen copy itself, for example an older install that still pulls from this repo.
Either way, the things that make it quick to fix:

- your OS, browser and browser version, and Python version
- the tool you pointed at the broker (Playwright, chrome-devtools-mcp, your own script)
- what `curl localhost:9223/status` prints
- the last lines of the broker log

## Running it locally

```bash
git clone https://github.com/nicedreamzapp/browser-broker && cd browser-broker
pip3 install websockets websocket-client
./launch-browser.sh   # your real profile, CDP on :9229
./install.sh          # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

Windows and Linux steps are in `docs/install.md`.

## Tests

The suite in `tests/` runs the real proxy against a fake browser, so it needs no browser and no
network. CI runs it on every push and pull request.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Pull requests

Please send pull requests to
[browser-agent](https://github.com/nicedreamzapp/browser-agent), not here. Small and focused is
easiest to review, and for big changes, open an issue first so we can talk it through.

Please follow the [code of conduct](CODE_OF_CONDUCT.md).
