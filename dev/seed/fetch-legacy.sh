#!/bin/sh
# Copy the legacy (Matrix-era bsbot) content index from a server into dev/dataset/:
# only the `documents` table (course/section/module structure and text) and the file
# blobs. Embeddings, chunks, PII tokens and Matrix credentials are never copied.
#
#   dev/seed/fetch-legacy.sh "ssh user@host" <container> [out-dir]
#   dev/seed/fetch-legacy.sh "sshm my-profile --" bsbot-api-1
#
# The first argument is the command prefix that runs a shell command on the server.
# Read-only on the server apart from a temporary file in the container's /tmp.
set -eu
remote=$1
container=$2
out=${3:-$(dirname "$0")/../dataset}
mkdir -p "$out"

snapshot=/tmp/legacy-documents.db
# One statement under the WAL snapshot: a consistent copy of `documents` only.
$remote "docker exec $container python -c \"
import sqlite3, os
if os.path.exists('$snapshot'): os.remove('$snapshot')
d = sqlite3.connect('file:$snapshot', uri=True)
d.execute(\\\"attach 'file:/data/index.db?mode=ro' as s\\\")
d.execute('create table documents as select * from s.documents')
d.commit()
\""
$remote "docker exec $container sh -c 'tar -cf - -C /tmp legacy-documents.db -C /data blobs; rm -f $snapshot'" \
  | tar -xf - -C "$out"
mv "$out/legacy-documents.db" "$out/documents.db"
echo "fetched into $out: $(du -sh "$out" | cut -f1)"
