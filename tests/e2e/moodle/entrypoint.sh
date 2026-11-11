#!/bin/sh
# Runs before Apache starts (base image hook): install Moodle once, seed, start cron.
set -e
cd /var/www/html

until php -r 'exit(@pg_connect("host=postgres dbname=moodle user=moodle password=moodle") ? 0 : 1);'; do
  echo "waiting for postgres"; sleep 2
done

installed=$(php -r '$c=pg_connect("host=postgres dbname=moodle user=moodle password=moodle");
  $r=pg_fetch_row(pg_query($c,"SELECT to_regclass(\x27mdl_config\x27) IS NOT NULL")); echo $r[0];')
if [ "$installed" != "t" ]; then
  php admin/cli/install_database.php --agree-license --lang=en \
    --adminuser=admin --adminpass='Admin1234!' --adminemail=admin@example.invalid \
    --fullname='E2E Moodle' --shortname=e2e
fi
php admin/cli/upgrade.php --non-interactive >/dev/null

until [ -s /shared/site-secret ]; do echo "waiting for the API's site secret"; sleep 2; done
php /opt/e2e/seed.php
chown -R www-data:www-data /var/moodledata

# Cron (it keeps itself alive and polls): the plugin's outbox drain and adhoc tasks.
( while true; do su -s /bin/sh www-data -c 'php admin/cli/cron.php' >>/tmp/cron.log 2>&1; sleep 5; done ) &
