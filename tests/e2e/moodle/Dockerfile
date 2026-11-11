# Stock Moodle for end-to-end tests: moodlehq's PHP/Apache image + a Moodle release.
# Build context is the repository root (see docker-compose.yml).
FROM moodlehq/moodle-php-apache:8.3

ARG MOODLE_BRANCH=MOODLE_405_STABLE
RUN curl -fsSL "https://github.com/moodle/moodle/archive/refs/heads/${MOODLE_BRANCH}.tar.gz" \
      | tar -xz --strip-components=1 -C /var/www/html \
 && mkdir -p /var/moodledata

COPY moodle/local_llmchat /var/www/html/local/llmchat
COPY moodle/block_llmchat /var/www/html/blocks/llmchat
COPY tests/e2e/moodle/config.php /var/www/html/config.php
COPY tests/e2e/moodle/seed.php /opt/e2e/seed.php
# The base image's entrypoint runs /docker-entrypoint.d/*.sh, then starts Apache.
COPY tests/e2e/moodle/entrypoint.sh /docker-entrypoint.d/50-e2e-setup.sh
RUN chmod +x /docker-entrypoint.d/50-e2e-setup.sh \
 && chown -R www-data:www-data /var/moodledata /var/www/html
