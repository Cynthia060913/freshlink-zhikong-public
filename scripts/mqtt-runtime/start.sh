#!/bin/sh
set -eu
mkdir -p /tmp/runtime-store
chown mosquitto:mosquitto /tmp/runtime-store
# -c refuses an existing file, including a file returned by mktemp. Create a
# private directory, populate a previously absent file, then replace only this
# fixture's password file. Retain runtime-store sessions across broker restarts.
password_dir="$(mktemp -d /tmp/runtime-auth.XXXXXX)"
password_file="$password_dir/passwords"
trap 'rm -f "$password_file"; rmdir "$password_dir"' EXIT
mosquitto_passwd -b -c "$password_file" test-controller fixture-controller-only
mosquitto_passwd -b "$password_file" test-actuator fixture-actuator-only
mosquitto_passwd -b "$password_file" test-sensor fixture-sensor-only
chmod 644 "$password_file"
mv -f "$password_file" /tmp/runtime-passwords
rmdir "$password_dir"
trap - EXIT
exec mosquitto -c /fixtures/mosquitto.conf
