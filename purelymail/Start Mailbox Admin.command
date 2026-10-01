#!/bin/bash
# Double-click this file in Finder to start BGYHub Mailbox Admin.
# The first time, it installs what the app needs and puts a
# "BGYHub Mailbox Admin" icon on your Desktop; use that icon from then on.
"$(cd "$(dirname "$0")" && pwd)/admin/launch.sh" --install-app
status=$?
if [ $status -eq 0 ]; then
  echo
  echo "BGYHub Mailbox Admin is open in your browser. You can close this window."
fi
exit $status
