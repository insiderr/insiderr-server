#!/bin/bash

if [ -z "$(pgrep -f dev_appserver)" ]; then
    echo "error: local server not running, see run-local.sh"
    exit 1
fi

# Fail on 1st error

if [ -n "$(which flake8)" ]; then
    flake8 isrv || exit 1
    flake8 tests || exit 1
fi

PYTHONPATH=${PWD} python2 tests/test_api.py -v $@
