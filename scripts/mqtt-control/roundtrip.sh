#!/bin/sh
# One unretained QoS1 message, actual authenticated receiver, byte-exact receipt.
set -eu
case "$1" in
  command) publisher=test-controller; subscriber=test-actuator; pub_pass=fixture-controller-only; sub_pass=fixture-actuator-only; topic=freshlink/v1/devices/66666666-6666-4666-8666-666666666666/commands; file=command.json; receipt=command-receipt.json ;;
  ack) publisher=test-actuator; subscriber=test-controller; pub_pass=fixture-actuator-only; sub_pass=fixture-controller-only; topic=freshlink/v1/devices/66666666-6666-4666-8666-666666666666/acks; file=ack.json; receipt=ack-receipt.json ;;
  telemetry) publisher=test-sensor; subscriber=test-controller; pub_pass=fixture-sensor-only; sub_pass=fixture-controller-only; topic=freshlink/v1/devices/11111111-1111-4111-8111-111111111111/telemetry; file=after.json; receipt=telemetry-receipt.json ;;
  *) exit 2 ;;
esac
mosquitto_sub -h 127.0.0.1 -V mqttv5 -q 1 -u "$subscriber" -P "$sub_pass" -t "$topic" -C 1 -W 10 -N > "/evidence/$receipt" &
receiver=$!
trap 'kill "$receiver" 2>/dev/null || true' EXIT
sleep 1
mosquitto_pub -h 127.0.0.1 -V mqttv5 -q 1 -u "$publisher" -P "$pub_pass" -t "$topic" -f "/evidence/$file"
wait "$receiver"
cmp "/evidence/$file" "/evidence/$receipt"
