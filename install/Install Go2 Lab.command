#!/bin/bash
# Double-click this file to install the Go2 simulator on your Mac.
#
# All it does is download and run install/mac.sh from the project repository,
# which is public. Nothing here needs an administrator password.
clear
cat <<'BANNER'

  ┌──────────────────────────────────────────┐
  │   Go2 Lab — installing             │
  └──────────────────────────────────────────┘

  This takes a few minutes the first time and needs WiFi.
  You can close this window when it says Done.

BANNER
URL="https://raw.githubusercontent.com/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh"
if curl -fsSL "$URL" | bash; then
  status="finished"
else
  status="did NOT finish — copy the messages above and send them to your mentor"
fi
echo
echo "Installation $status."
echo
printf 'Press Return to close this window. '
read -r _
