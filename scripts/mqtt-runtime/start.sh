#!/bin/sh
set -eu
mkdir -p /tmp/runtime-store
chown mosquitto:mosquitto /tmp/runtime-store
# Build under a fresh root-owned name: rewriting a password file left by a
# previous broker run in sticky /tmp can fail under protected_regular.
# Replace only this fixture's password file; retain runtime-store sessions.
password_file="$(mktemp /tmp/runtime-passwords.XXXXXX)"
trap 'rm -f "$password_file"' EXIT
mosquitto_passwd -b -c "$password_file" test-controller fixture-controller-only
mosquitto_passwd -b "$password_file" test-actuator fixture-actuator-only
mosquitto_passwd -b "$password_file" test-sensor fixture-sensor-only
chmod 644 "$password_file"
mv -f "$password_file" /tmp/runtime-passwords
trap - EXIT
exec mosquitto -c /fixtures/mosquitto.conf
