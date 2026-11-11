# Moodle for the local dev stack. Same install and seed as the e2e image, but served at
# http://localhost:8080 (Apache listens on 8080 so the URL is identical inside and
# outside the container — see dev/docker-compose.yml). Build context: repository root.
FROM moodlehq/moodle-php-apache:8.3

ARG MOODLE_BRANCH=MOODLE_405_STABLE
RUN curl -fsSL "https://github.com/moodle/moodle/archive/refs/heads/${MOODLE_BRANCH}.tar.gz" \
      | tar -xz --strip-components=1 -C /var/www/html \
 && mkdir -p /var/moodledata

RUN sed -i 's/^Listen 80$/Listen 8080/' /etc/apache2/ports.conf \
 && sed -i 's/<VirtualHost \*:80>/<VirtualHost *:8080>/' /etc/apache2/sites-available/000-default.conf \
 && grep -q '^Listen 8080$' /etc/apache2/ports.conf \
 && grep -q '<VirtualHost \*:8080>' /etc/apache2/sites-available/000-default.conf

# Baked in for the first install; docker-compose.yml bind-mounts the live plugin over it.
COPY moodle/local_llmchat /var/www/html/local/llmchat
COPY moodle/block_llmchat /var/www/html/blocks/llmchat
COPY dev/moodle/config.php /var/www/html/config.php
COPY tests/e2e/moodle/seed.php /opt/e2e/seed.php
COPY tests/e2e/moodle/entrypoint.sh /docker-entrypoint.d/50-e2e-setup.sh
# Optional real dataset (dev/dataset/, bind-mounted at /opt/dataset): see dev/seed/.
COPY dev/moodle/seed-dataset.php /opt/dev/seed-dataset.php
COPY dev/moodle/dataset.sh /docker-entrypoint.d/60-dataset.sh
RUN chmod +x /docker-entrypoint.d/50-e2e-setup.sh /docker-entrypoint.d/60-dataset.sh \
 && chown -R www-data:www-data /var/moodledata /var/www/html
