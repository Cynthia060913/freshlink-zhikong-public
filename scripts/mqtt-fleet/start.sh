#!/bin/sh
set -eu
# Synthetic local test credentials only. No host services or real recipients.
mosquitto_passwd -b -c /tmp/fleet-passwords fixture-controller fixture-controller-only
i=1
while [ "$i" -le 7 ]; do
  mosquitto_passwd -b /tmp/fleet-passwords "fixture-sensor-$i" "fixture-sensor-$i-only"
  mosquitto_passwd -b /tmp/fleet-passwords "fixture-actuator-$i" "fixture-actuator-$i-only"
  i=$((i + 1))
done
chmod 644 /tmp/fleet-passwords
exec mosquitto -c /fixtures/mosquitto.conf
