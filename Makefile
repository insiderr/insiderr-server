db_path=~/.insiderr/datastore

all:
	$(error please pick a target)

venv: requirements.txt
	virtualenv venv
	. ./venv/bin/activate && pip install -r requirements.txt


run:
	mkdir -p $(db_path)
	python2 /opt/google_appengine/dev_appserver.py . \
	    --skip_sdk_update_check \
	    --storage_path=$(db_path)

run-local:
	mkdir -p $(db_path)
	python2 /opt/google_appengine/dev_appserver.py . \
	    --skip_sdk_update_check \
	    --storage_path=$(db_path)\
	    --host=0.0.0.0 \
        --admin_host=0.0.0.0

test:
	./run-tests.sh


tags:
	ctags -R isrv tests


.PHONY: all run test tags
