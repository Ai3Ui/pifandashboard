# Pi Fan Dashboard

A lightweight Raspberry Pi PWM fan controller and web dashboard. It shows CPU temperature, fan duty, CPU use, and memory use, and now lets you edit the fan curve from the browser.

This fork's installer is designed to coexist with Pi-hole and other web services: it serves itself on port `8088` and does not install nginx or alter `/var/www`.

## Hardware

The default wiring uses a 5 V fan with its PWM/control wire connected to physical pin 8 (`GPIO14`, BCM numbering). Power the fan from 5 V and ground. GPIO14 must not also be used by the serial console.

Raspberry Pi 3 and 4 are supported through `RPi.GPIO` or the compatible `rpi-lgpio` implementation. The Raspberry Pi 5 fan header uses a different interface and is not supported by this controller.

## Install

```bash
git clone https://github.com/Ai3Ui/pifandashboard.git
cd pifandashboard
sudo ./install.sh
```

Then open `http://PI_ADDRESS:8088/`.

Options are available for a different port, GPIO, or initial fan curve:

```bash
sudo ./install.sh --port 8088 --gpio 14 --start-temp 45 --full-temp 75 --min-duty 45
```

The installer:

- prefers `python3-rpi-lgpio` on current Raspberry Pi OS, with `python3-rpi.gpio` as a fallback;
- checks whether the selected port is already occupied;
- detects the GPIO14 serial-console conflict and gives the exact command to resolve it;
- installs isolated services and data under `/opt/pifandashboard`, `/etc/pifandashboard`, and `/var/lib/pifandashboard`;
- leaves nginx, Pi-hole, and `/var/www` unchanged.

## Fan curve

The default curve keeps the fan off below 45 °C, starts it at 45%, and increases it linearly to 100% at 75 °C. Open **Fan Settings** in the dashboard to change these values. Temperature and speed changes apply within five seconds; a GPIO change requires:

```bash
sudo systemctl restart pifandashboard-fan.service
```

## Service checks

```bash
systemctl status pifandashboard-fan.service
systemctl status pifandashboard-web.service
journalctl -u pifandashboard-fan.service -n 50
```

The original project documentation remains in [`docs/README.md`](docs/README.md). Licensed under the MIT License in [`docs/LICENSE`](docs/LICENSE).
