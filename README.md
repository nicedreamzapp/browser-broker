# Moved: browser-broker now lives in browser-agent

**browser-broker now lives in [github.com/nicedreamzapp/browser-agent/tree/main/broker](https://github.com/nicedreamzapp/browser-agent/tree/main/broker).**

The agent does the work; the broker makes it safe to share your real, logged-in browser with it. The two are one project now, with one README: [nicedreamzapp/browser-agent](https://github.com/nicedreamzapp/browser-agent). The broker's full git history came with it.

New installs (macOS):

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/get.sh | bash
```

Windows: clone browser-agent and run `broker\windows\install.ps1` (details in the [main README](https://github.com/nicedreamzapp/browser-agent#windows)).

The code in this repo stays as it is, frozen at the last standalone version, so existing installs that pull from here keep working. New work, issues and pull requests go to [browser-agent](https://github.com/nicedreamzapp/browser-agent/issues).
