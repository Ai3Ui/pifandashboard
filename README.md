# Pi Fan Dashboard

A lightweight, authenticated Raspberry Pi PWM fan controller and fleet dashboard. It displays CPU temperature, fan duty, CPU, memory, storage, load, uptime, history, and the configured fan curve without Bootstrap, Chart.js, Node.js, or `psutil`.

This reviewed fork uses two restricted, non-root services and a transactional installer. It does not install nginx, change Pi-hole, edit UFW, or touch `/var/www`.

## Hardware

The default control output is physical pin 8 (`GPIO14`, BCM numbering). GPIO12, GPIO13, GPIO18, and GPIO19 are also accepted. The application uses software PWM through the installed `RPi.GPIO`-compatible provider.

Power the fan from the correct 5 V and ground pins; never power a fan motor from a GPIO output. Connect only a manufacturer-designated PWM/control input to the selected GPIO, and do not assume wire purpose from colour alone. A conventional three-wire fan whose third lead is tachometer-only needs suitable external switching hardware instead.

The controller targets Raspberry Pi 3 and 4 GPIO-connected fans. It does not control the dedicated Raspberry Pi 5 fan header.

## Install or upgrade

```bash
git clone https://github.com/Ai3Ui/pifandashboard.git
cd pifandashboard
sudo ./install.sh
```

The installer preserves an existing dashboard port. On a fresh install it tries `8088`, then selects the first free TCP port through `8188`. It prints the final URL and a one-time generated dashboard password. An explicitly requested occupied port fails safely instead of silently changing it.

Useful examples:

```bash
sudo ./install.sh --port 8095
sudo ./install.sh --port auto --prompt-port
sudo ./install.sh --gpio 18
sudo ./install.sh --start-temp 48 --full-temp 72 --min-duty 50 --hysteresis 3
sudo ./install.sh --reset-password
```

Individual curve options patch only those values on an upgrade. `--update-curve` deliberately replaces the complete curve with the supplied values and defaults for omitted values.

The installer validates configuration and mutable state before use, stages and compiles a complete release, stops the old writers, atomically swaps the code, and health-checks both services. If deployment fails it restores the previous code, units, settings, Pi list, credentials, port, and prior enabled/running state.

## Network security

The dashboard accepts only loopback and RFC1918 private IPv4 clients, rate-limits endpoints, requires a password for settings and fleet management, and makes peer requests server-side with redirect, size, address, and hard deadline controls.

The default LAN URL is plain HTTP. Use it only on a trusted home LAN: another device able to sniff or alter that network could capture the password or session token. For an untrusted LAN, bind to loopback and use SSH forwarding:

```bash
sudo ./install.sh --bind 127.0.0.1
ssh -L 8088:127.0.0.1:8088 USER@PI_ADDRESS
```

If the installer selected a different port, use that port on both sides of the tunnel. A properly configured local HTTPS reverse proxy is another option, but the installer intentionally does not modify an existing web server.

## Fan behaviour

The default curve starts at 45 °C and 45% duty, rises linearly to 100% at 75 °C, and uses 2 °C hysteresis to prevent rapid on/off cycling. A stopped fan receives a short 100% startup boost. Invalid configuration, sensor errors, and controller shutdown all request full-speed fail-safe output.

Start temperature, full temperature, minimum duty, and hysteresis can be changed in **Fan settings** and apply within five seconds. GPIO is intentionally read-only in the browser; change it with `sudo ./install.sh --gpio N` after checking the wiring.

## Operations

```bash
systemctl status pifandashboard-fan.service pifandashboard-web.service
journalctl -u pifandashboard-fan.service -n 50
cat /run/pifandashboard/status.json
```

Reset the dashboard password locally without placing a password on the command line:

```bash
sudo /usr/bin/python3 /opt/pifandashboard/app/auth.py reset /etc/pifandashboard/auth.json
```

The reset command prints a new generated password once and invalidates existing sessions.

Remove the code and services while preserving settings and credentials:

```bash
sudo ./uninstall.sh
```

Use `sudo ./uninstall.sh --purge` and confirm at the terminal to remove persistent state as well. Shared OS packages, Pi-hole, nginx, UFW, and unrelated files are retained.

## Development checks

```bash
python3 -m unittest discover -v
bash -n install.sh uninstall.sh fandashboardsetup.sh fandashboarduninstaller.sh
node --check webinterface/fandashboard/js/script.js
```

See [`docs/DETAILS.md`](docs/DETAILS.md) for the current architecture. The project is provided under the MIT License in [`docs/LICENSE`](docs/LICENSE).
