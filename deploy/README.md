# Deployment (systemd user service, Tailscale access)

The live engine runs as a systemd **user** service, so it survives reboots
(linger is enabled for the user on `desktop-ai`).

## Install / update

```bash
mkdir -p ~/.config/systemd/user
cp deploy/business-analyzer.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now business-analyzer.service
loginctl enable-linger "$USER"   # already enabled on desktop-ai
```

Logs: `journalctl --user -u business-analyzer.service -f`

## Access

- Same machine: http://localhost:8765
- Any device on the tailnet (e.g. phone with the Tailscale app): http://100.93.38.87:8765
  (the server binds 0.0.0.0; the tailnet IP is the Tailscale address of `desktop-ai`)

Nothing is exposed to the public internet — the tailnet IP is only reachable
by devices in the tailnet.
