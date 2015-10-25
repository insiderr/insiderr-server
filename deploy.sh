#!/bin/bash


st=$(hg status)
if [ -n "$st" ]; then
    echo "error: uncommited changes"
    exit 1
fi

python2 /opt/google_appengine/appcfg.py update --oauth2 .
hg tag -f deployed
hg push
