#!/bin/sh
set -eu
# Public fixture passwords, generated only into this disposable container.
mosquitto_passwd -b -c /tmp/smoke-passwords smoke-device disposable-device-only
mosquitto_passwd -b /tmp/smoke-passwords smoke-observer disposable-observer-only
chmod 644 /tmp/smoke-passwords
exec mosquitto -c /fixtures/mosquitto.conf
