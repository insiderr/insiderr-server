''' Maintenance code intended to be used in the
    App Engine Interactive Console.
'''

''' Deleting items (except channels, users and tokens)
'''
from google.appengine.ext import ndb
from isrv import db

ndb.delete_multi(db.Post.query().iter(keys_only=True))
ndb.delete_multi(db.Comment.query().iter(keys_only=True))
ndb.delete_multi(db.Update.query().iter(keys_only=True))
ndb.delete_multi(db.UpVote.query().iter(keys_only=True))
ndb.delete_multi(db.DownVote.query().iter(keys_only=True))

''' Deleting a post
'''
from google.appengine.ext import ndb
from isrv import db

post_key = '<key-from-datastore>'
ndb_key = ndb.Key(urlsafe=post_key)
ndb.delete_multi(db.Comment.query(ancestor=ndb_key).iter(keys_only=True))
ndb.delete_multi(db.Update.query(ancestor=ndb_key).iter(keys_only=True))
ndb.delete_multi(db.UpVote.query(ancestor=ndb_key).iter(keys_only=True))
ndb.delete_multi(db.DownVote.query(ancestor=ndb_key).iter(keys_only=True))
ndb_key.delete()