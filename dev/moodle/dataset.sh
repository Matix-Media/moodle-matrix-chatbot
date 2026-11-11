#!/bin/sh
# Runs after 50-e2e-setup.sh (base image hook): seed the dataset mounted at
# /opt/dataset if it has a manifest.json (see dev/seed/README.md). No manifest → no-op.
# A broken dataset is reported but never keeps Moodle from starting.
set -e
if [ -f /opt/dataset/manifest.json ]; then
  php /opt/dev/seed-dataset.php /opt/dataset \
    || echo "dataset seed FAILED (see above); Moodle starts without it"
  chown -R www-data:www-data /var/moodledata
fi
