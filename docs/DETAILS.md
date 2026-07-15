# Architecture and security details

## Runtime flow

```text
thermal sensor -> non-root fan controller -> /run/pifandashboard/status.json
                         |                         |
                         v                         v
                    GPIO software PWM      non-root Waitress/Flask API
                                                     |
browser <- same-origin HTML/CSS/JavaScript <- authenticated fleet proxy
                                                     |
                                           private peer /status APIs
```

Every installed Pi runs two systemd services:

- `pifandashboard-fan.service` reads the thermal sensor and validated curve, drives one installer-fixed GPIO, and atomically publishes status. It has GPIO device access but no network access.
- `pifandashboard-web.service` serves the dependency-free frontend and Flask API with Waitress. It can read status, edit the curve and Pi list, and make restricted private-LAN peer requests, but it has no GPIO device access.

The services use separate system users. Their shared group permits only the required status/configuration handoff; a second read-only group protects the password hash from the fan process. Both units remove Linux capabilities, restrict writable paths, devices, address families, system calls, memory, tasks, and file descriptors, and enable the applicable systemd sandbox controls.

## Files

| Path | Purpose and ownership |
| --- | --- |
| `/opt/pifandashboard` | Root-owned immutable application release |
| `/opt/pifandashboard.previous` | One rollback release |
| `/etc/pifandashboard/web.env` | Root-only bind address and selected port |
| `/etc/pifandashboard/fan.env` | Root-only installer-approved GPIO |
| `/etc/pifandashboard/auth.json` | Root-owned PBKDF2 password record, readable by the web auth group |
| `/var/lib/pifandashboard/config/fan.json` | Validated mutable fan curve |
| `/var/lib/pifandashboard/web/pi_list.json` | Validated private IPv4 fleet list, maximum 16 entries |
| `/run/pifandashboard/status.json` | Atomic, transient controller status |

The installer rejects symlinks and irregular state/source files, stops writers before its final state checks, uses unique same-directory temporary files, and restores backups if service or HTTP health checks fail.

## API boundaries

- `/health` is a minimal liveness endpoint.
- `/status` exposes sanitised local telemetry only to loopback/RFC1918 clients so other dashboard nodes can read it without sharing credentials.
- `/auth/*`, `/fan-config`, Pi-list endpoints, and `/fleet-status` require an in-memory bearer session obtained with the local password.
- The browser contacts only its own origin. `/fleet-status` validates the stored list and performs bounded server-side HTTP requests to literal RFC1918/loopback IPv4 addresses. Redirects, proxy environment variables, chunked bodies, oversized headers/bodies, slow-drip responses, and requests exceeding the hard deadline are rejected.

Strict response headers include a same-origin Content Security Policy, frame denial, MIME sniffing denial, no-referrer policy, a restrictive permissions policy, and no-store caching for credentials and managed state. Dynamic UI content is created with DOM text properties rather than HTML parsing.

## Resource strategy

The backend reads Linux `/proc`, `statvfs`, and the thermal sysfs file directly, caches host metrics for one second, and samples the controller every five seconds. It does not use `psutil`. The frontend uses small custom canvas renderers instead of framework/chart bundles, pauses polling in hidden tabs, polls the fleet every ten seconds, caps device-pixel ratio and history, and reuses cards.

Waitress uses four request threads. Fleet reads use one global bounded eight-worker executor, a five-second cache, a maximum of 16 peers, a 32 KiB response cap, and a two-second absolute per-peer deadline. systemd enforces 128 MiB for the web service and 64 MiB for the controller as last-resort ceilings; normal use should be far below these limits.

## Residual limitations

- Default LAN access is HTTP, not TLS. It is suitable only for a trusted LAN; use `--bind 127.0.0.1` with SSH forwarding or configure a separate HTTPS reverse proxy on an untrusted network.
- Sessions are intentionally memory-only and are lost on web-service restart.
- This software drives GPIO-connected control hardware, not the Raspberry Pi 5 dedicated fan header.
- A GPIO high level is the fail-safe assumption for the supported wiring. Confirm that this matches any different fan driver before deployment.
