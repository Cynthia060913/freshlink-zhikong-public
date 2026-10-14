#!/bin/sh
set -eu
mosquitto_passwd -b -c /tmp/control-passwords test-controller fixture-controller-only
mosquitto_passwd -b /tmp/control-passwords test-actuator fixture-actuator-only
mosquitto_passwd -b /tmp/control-passwords test-sensor fixture-sensor-only
chmod 644 /tmp/control-passwords
exec mosquitto -c /fixtures/mosquitto.conf
