# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report them privately through GitHub: go to the **Security** tab of this repo and click **Report a vulnerability** ([direct link](https://github.com/nicedreamzapp/browser-broker/security/advisories/new)). Only the maintainer sees the report. If the problem is also in the current code, you can report it on [browser-agent](https://github.com/nicedreamzapp/browser-agent/security/advisories/new) instead.

Helpful details: what you found, how to reproduce it, which commit, your OS, browser and browser version, and the tool you had connected to the broker.

I aim to reply within 3 days and to ship a fix or a clear plan within 30 days, depending on severity. You will be credited in the advisory unless you ask not to be.

## Supported versions

This repo is frozen. Fixes go into the broker in [browser-agent](https://github.com/nicedreamzapp/browser-agent) on `main`. A serious problem that affects installs still pulling from this repo may get a fix here too. Please move to browser-agent if you can.

## What matters most here

The broker sits in front of your real, logged-in browser, so the parts where a mistake could hurt someone:

- `proxy.py`, which listens on `127.0.0.1` ports 9222 and 9223 and keeps each agent out of your tabs and other agents' tabs
- `launch-browser.sh` and `windows/launch-browser.ps1`, which start your browser with remote debugging on
- `get.sh`, which people run with one `curl | bash` line, and the installers `install.sh` and `windows/install.ps1`
- `bb-show` and `bb-peek`, which read what a page shows, including filled-in form fields (passwords and card numbers are meant to be masked)
