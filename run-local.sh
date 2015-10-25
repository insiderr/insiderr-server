#!/bin/bash

mkdir ../log > /dev/null 2>&1
mkdir ../datastore > /dev/null 2>&1

python2 /opt/google_appengine/dev_appserver.py . \
    --skip_sdk_update_check \
    --host=0.0.0.0 \
    --admin_host=0.0.0.0 \
    --storage_path=../datastore/ \
    $@  >> ../log/serverlog.txt 2>&1 &

