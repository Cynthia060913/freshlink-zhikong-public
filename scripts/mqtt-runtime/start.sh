#!/bin/sh
set -eu
mkdir -p /tmp/runtime-store
chown mosquitto:mosquitto /tmp/runtime-store
mosquitto_passwd -b -c /tmp/runtime-passwords test-controller fixture-controller-only
mosquitto_passwd -b /tmp/runtime-passwords test-actuator fixture-actuator-only
mosquitto_passwd -b /tmp/runtime-passwords test-sensor fixture-sensor-only
chmod 644 /tmp/runtime-passwords
exec mosquitto -c /fixtures/mosquitto.conf
