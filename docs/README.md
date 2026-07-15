# Pi Fan Dashboard documentation

This folder previously described the first-generation nginx, `/var/www`, ports 10000/10001, Bootstrap, and Chart.js installation. That architecture has been removed and those instructions must not be used for the reviewed fork.

Use the repository's current [`README.md`](../README.md) for wiring cautions, installation, authentication, port selection, upgrades, and removal. See [`DETAILS.md`](DETAILS.md) for the present two-service architecture and security boundaries.

In particular, install from a complete checkout so the transactional installer has all reviewed source files:

```bash
git clone https://github.com/Ai3Ui/pifandashboard.git
cd pifandashboard
sudo ./install.sh
```

Do not download and execute a standalone legacy setup wrapper. The compatibility wrappers in this repository only delegate to the adjacent reviewed `install.sh` or `uninstall.sh`.
