# Changelog

## Unreleased

- Added robust CLI start/stop handling for non-systemd systems with pidfile support.
- Implemented application-level maintenance mode (forge pause-app / forge resume-app) to safely pause agents and block outbound model/API calls.
- Added a start-wrapper (/opt/forge/bin/forge-start) which writes PIDFile (/var/run/forge/forge.pid) and forwards signals to the uvicorn child.
- Updated systemd unit to use the start-wrapper and PIDFile; changed Restart policy to on-failure and increased TimeoutStopSec.
- CLI actions (start/stop/pause-app/resume-app) are audited into the DB logs table when possible.
- Added tests for maintenance mode and start/stop fallback.
- Updated README with operational guidance.
