from . import db

import webapp2
from google.appengine.api import taskqueue
from google.appengine.api import mail
from google.appengine.api import memcache
from google.appengine.api import app_identity

from datetime import datetime
from operator import itemgetter
from os import environ
import httplib
import json
import logging as log

update_task_url = '/tasks/publisher'
flag_task_url = '/tasks/flag'
feedback_task_url = '/tasks/feedback'
time_fmt = '%Y-%m-%dT%H:%M:%SZ'
hashkey = itemgetter('hash')


def is_local_srv():
    # Enable to allow '_t' access from anywhere...
    # return True
    return environ.get('SERVER_SOFTWARE', '').startswith('Development')


class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime(time_fmt)
        elif isinstance(obj, db.KeyType):
            return db.encode_key(obj)

        return json.JSONEncoder.default(self, obj)


def jsonify(obj):
    return json.dumps(obj, cls=JSONEncoder)


class RequestHandler(webapp2.RequestHandler):
    dbtype = None

    def get_user(self):
        token = self.request.headers.get('Authorization')
        if not token:
            log.error('no auth')
            self.abort(httplib.UNAUTHORIZED)

        user = db.User.from_token(token)
        if not user:
            log.error('unknown user')
            self.abort(httplib.UNAUTHORIZED)
        return user

    def assert_internal(self, header):
        if is_local_srv():
            return

        #  Make sure it's internal AppEngine request
        # (AppEngine will strip these headers from external requests)
        if header not in self.request.headers:
            self.abort(httplib.UNAUTHORIZED)

    def set_json_header(self):
        self.response.headers['Content-Type'] = 'application/json'

    def json_reply(self, obj):
        self.set_json_header()
        data = jsonify(obj)
        set_cached(self, data)
        self.response.write(data)

    def key_reply(self, obj):
        self.json_reply({'ok': True, 'key': obj.key})

    # Default get method
    def get(self, key=None):
        self.get_user()  # Make sure we're authorized
        if not key:
            log.error('no key')
            self.abort(httplib.BAD_REQUEST)

        obj = db.Model.from_key(key)
        if not obj:
            log.error('no such key: %s', key)
            self.abort(httplib.NOT_FOUND)

        name = self.dbtype.__name__.lower()
        # FIXME: Add key to dict
        self.json_reply({'ok': True, name: obj.to_dict()})

    def request_json(self):
        return json.loads(self.request.body)

    def get_param(self, name, conv, default):
        val = self.request.get(name)

        if not val:
            return default

        try:
            return conv(val)
        except ValueError:
            log.error('bad value for %s - %s', name, val)
            self.abort(httplib.BAD_REQUEST)


# NOTE: The below three are a hack to guard against Twisted sending requests
# mutiple times
def request_id(handler):
    return handler.request.get('rid')


def get_cached(handler):
    reqid = request_id(handler)
    if not reqid:
        log.warning('missing request id')

    return memcache.get(reqid)


def set_cached(handler, reply):
    rid = request_id(handler)
    if not rid:
        return

    memcache.set(rid, reply, 60)  # TODO: Find good timeout


def notify_update(channels, obj):
    msg = jsonify({
        'time': datetime.now(),
        'key': obj.key,
        'channels': channels,
    })
    taskqueue.add(url=update_task_url, params={'update': msg})


class RegisterHandler(RequestHandler):
    dbtype = db.User

    def post(self):
        try:
            data = self.request_json()
            pub_key = data.get('pub_key')
            if not pub_key:
                raise ValueError
        except ValueError:
            log.error('missing pub_key')
            self.abort(httplib.BAD_REQUEST)

        try:
            log.info('new user: pub_key=%s', pub_key)  # FIXME: Don't log?
            user = db.User.create(pub_key)
        except db.Duplicate:
            log.error('duplicate user: pub_key=%s', pub_key)
            self.abort(httplib.BAD_REQUEST)
        except db.Error as err:
            log.exception('cannot save - %s', err)
            self.abort(httplib.INTERNAL_SERVER_ERROR)

        log.info('new user key: %s', user.uid())
        self.key_reply(user)


class LoginHandler(RequestHandler):
    dbtype = db.User

    def post(self):
        try:
            data = self.request_json()
            key = data.get('key')
            if not key:
                raise ValueError
        except ValueError:
            log.error('missing key')
            self.abort(httplib.BAD_REQUEST)

        try:
            log.info('login user: key=%s', key)
            user = db.User.from_key(key)
            if not user:
                raise db.NotFound
            token = user.login()
        except db.NotFound:
            log.error('user not found: key=%s', key)
            self.abort(httplib.NOT_FOUND)
        except db.Error as err:
            log.exception('cannot login - %s', err)
            self.abort(httplib.INTERNAL_SERVER_ERROR)

        self.json_reply({'ok': True, 'token': token})


class PostsHandler(RequestHandler):
    dbtype = db.Post

    def post(self, ignored=None):
        user = self.get_user()

        out = get_cached(self)
        if out:
            self.set_json_header()
            self.request.write(out)
            return

        data = self.request_json()

        required = set([
            'role', 'role_text', 'theme', 'content', 'background', 'channels',
        ])
        # TODO: Better validator (type, size < 500 ...)
        missing = required - set(data)
        if missing:
            log.error('missing fields: %s', ', '.join(missing))
            self.abort(httplib.BAD_REQUEST)

        post = db.Post.create(
            user,
            data['content'],
            data['theme'],
            data['background'],
            data['channels'],
            data['role'],
            data['role_text'],
        )

        notify_update(post.channels, post)
        self.key_reply(post)


class CommentsHandler(RequestHandler):
    dbtype = db.Comment

    def post(self, post_key=None):
        user = self.get_user()

        out = get_cached(self)
        if out:
            self.set_json_header()
            self.request.write(out)
            return

        if not post_key:
            log.error('no post_key')
            self.abort(httplib.BAD_REQUEST)

        post = db.Post.from_key(post_key)
        if not post:
            log.error('unknown post - %s', post_key)
            self.abort(httplib.BAD_REQUEST)

        data = self.request_json()

        # HACK: backwards compatibility for older versions
        # TODO: remove as soon as possible
        data['role'] = data.get('role', 'anonymous')
        data['role_text'] = data.get('role_text', 'someone')

        required = set(['role', 'role_text', 'content'])
        # TODO: Better validator (type, size < 500 ...)
        missing = required - set(data)
        if missing:
            log.error('missing fields: %s', ', '.join(missing))
            self.abort(httplib.BAD_REQUEST)

        comment = db.Comment.create(
            post,
            user,
            data['content'],
            data['role'],
            data['role_text'],
        )
        notify_update(post.channels, comment)
        self.key_reply(comment)

    def get(self, post_key=None):
        if not post_key:
            log.error('no post key')
            self.abort(httplib.BAD_REQUEST)

        post = db.Post.from_key(post_key)
        if not post:
            log.error('no such post - %s', post_key)
            self.abort(httplib.NOT_FOUND)

        comments = [comm.to_dict() for comm in post.comments()]
        self.json_reply({'ok': True, 'comments': comments})


class VotesHandler(RequestHandler):
    dbtype = db.UpVote

    def post(self, key=None, direction=None):
        user = self.get_user()

        out = get_cached(self)
        if out:
            self.set_json_header()
            self.request.write(out)
            return

        if not (direction and key):
            log.error('bad path')
            self.abort(httplib.BAD_REQUEST)

        direction = direction.lower()
        if direction not in ('up', 'down'):
            log.error('bad direction')
            self.abort(httplib.BAD_REQUEST)

        obj = db.Model.from_key(key)
        if not obj:
            log.error('object not found - %s', key)
            self.abort(httplib.NOT_FOUND)

        if not isinstance(obj, db.Votable):
            log.error('bad vote object - %s', obj.__class__)
            self.abort(httplib.BAD_REQUEST)

        vote = db.Vote.create(obj, user, direction)
        notify_update(obj.parent_post().channels, vote)
        resp = {
            'ok': True,
            'key': obj.key,
            'upvote_count': len(obj.upvotes()),
            'downvote_count': len(obj.downvotes()),
        }
        self.json_reply(resp)

    def delete(self, key=None, direction=None):
        user = self.get_user()

        out = get_cached(self)
        if out:
            self.set_json_header()
            self.request.write(out)
            return

        if not (direction and key):
            log.error('bad path')
            self.abort(httplib.BAD_REQUEST)

        direction = direction.lower()
        if direction not in ('up', 'down'):
            log.error('bad direction')
            self.abort(httplib.BAD_REQUEST)

        obj = db.Model.from_key(key)
        if not obj:
            log.error('object not found - %s', key)
            self.abort(httplib.NOT_FOUND)

        if not isinstance(obj, db.Votable):
            log.error('bad vote object - %s', obj.__class__)
            self.abort(httplib.BAD_REQUEST)

        db.Vote.delete(obj, user, direction)
        # TODO: how to notify when a vote has been removed?
        # notify_update(obj.parent_post().channels, vote)
        resp = {
            'ok': True,
            'key': obj.key,
            'upvote_count': len(obj.upvotes()),
            'downvote_count': len(obj.downvotes()),
        }
        self.json_reply(resp)


def str2dt(v):
    return datetime.strptime(v, time_fmt)


def identity(obj):
    return obj


def uniquify(items, keyfn=identity):
    seen = set()
    return [seen.add(keyfn(item)) or item
            for item in items
            if keyfn(item) not in seen]


def update2dict(update):
    # TODO: this isn't optimal but ensures correctness
    if update.post:
        post = update.post.get()
        if post:
            return {
                'obj': post.to_dict(),
                'hash': update.created,
            }


class ChannelsHandler(RequestHandler):
    dbtype = db.Channel

    def encode_hash(self, created, key):
        return '{}|{}'.format(
            datetime.strftime(created, time_fmt),
            db.encode_key(key))

    def parse_hash(self):
        since = datetime.now()
        key = None
        hash = self.request.get('hash')
        if hash:
            try:
                parts = hash.split('|')
                since = str2dt(parts[0])
                if len(parts) > 1:
                    key = db.decode_key(parts[1])
            except:
                log.error('invalid hash - %s', hash)
                self.abort(httplib.BAD_REQUEST)
        return since, key

    def post2obj(self, post):
        return {
            'post': post.to_dict(),
            'hash': self.encode_hash(post.created, post.key),
        }

    def get(self, chan_key=None):
        self.get_user()  # Make sure we're authenticated
        if not chan_key:
            return self.list_channels()

        # Updates on a channel
        chan = db.Channel.from_key(chan_key)
        if not chan:
            log.error('unknown channel - %s', chan_key)
            self.abort(httplib.NOT_FOUND)

        since, key = self.parse_hash()
        count = self.get_param('count', int, 100)
        objs = [self.post2obj(post) for post in chan.find(since, key, count)]
        self.json_reply({'ok': True, 'updates': objs})

    def list_channels(self):
        channels = [chan.to_dict() for chan in db.Channel.iter_all()]
        self.json_reply({'ok': True, 'channels': channels})


class UpdatesHandler(RequestHandler):
    dbtype = db.Update

    def get(self):
        self.get_user()  # Make sure we're authenticated

        keys = self.request.get('key', allow_multiple=True)

        if not keys:
            log.error('no keys')
            self.abort(httplib.BAD_REQUEST)

        sample_time = datetime.now()
        since = self.get_param('hash', str2dt, datetime(1970, 1, 1))
        kind = self.request.get('kind')
        keys = uniquify(keys)
        try:
            keys = [db.decode_key(key) for key in keys]
        except TypeError as err:
            log.error('bad keys - %s', err)
            new_keys = []
            for key in keys:
                try:
                    new_keys.append(db.decode_key(key))
                except:
                    pass
            keys = new_keys

        if kind:
            updates = db.Update.updates_for(keys, since, kinds=[kind])
        else:
            updates = db.Update.updates_for(keys, since)
        objs_dicts = (update2dict(update) for update in updates if update.post)
        objs = sorted(
            (od for od in objs_dicts if od),
            key=hashkey)

        self.json_reply({'ok': True, 'hash': sample_time, 'updates': objs})


class ItemsHandler(RequestHandler):
    dbtype = db.Model

    def get(self):
        self.get_user()  # Make sure we're authenticated

        keys = self.request.get('key', allow_multiple=True)

        if not keys:
            log.error('no keys')
            self.abort(httplib.BAD_REQUEST)

        sample_time = datetime.now()

        try:
            objs = \
                [obj.to_dict() for obj in db.get_multi(uniquify(keys)) if obj]
        except TypeError as err:
            log.error('bad keys - %s', err)
            self.abort(httplib.BAD_REQUEST)

        self.json_reply({'ok': True, 'objects': objs, 'hash': sample_time})


class FlagHandler(RequestHandler):
    dbtype = db.Flag

    def post(self, key=None):
        self.get_user()  # Make sure we're authenticated

        if not key:
            log.error('no key')
            self.abort(httplib.BAD_REQUEST)

        try:
            key = db.decode_key(key)
        except TypeError as err:
            log.error('bad key - %s (%s)', key, err)
            self.abort(httplib.BAD_REQUEST)

        flag = db.Flag.create(key, datetime.now())
        taskqueue.add(url=flag_task_url, params={'key': key})
        self.key_reply(flag)


class FeedbackHandler(RequestHandler):
    dbtype = db.Feedback

    def post(self):
        user = self.get_user()
        content = self.request.body.strip()
        if not content:
            log.error('no content in body')
            self.abort(httplib.BAD_REQUEST)

        fb = db.Feedback.create(user, content)
        params = {'user': user.key, 'content': content}
        taskqueue.add(url=feedback_task_url, params=params)
        self.key_reply(fb)


class IconsHandler(RequestHandler):
    def get(self):
        self.set_json_header()
        with open('icons.json') as fo:
            for chunk in iter(lambda: fo.read(1024), ''):
                self.response.write(chunk)


class UpdateTask(RequestHandler):
    def post(self):
        self.assert_internal('X-Appengine-QueueName')
        msg = self.request.get('update')
        if msg is None:
            log.error('no message')
            self.abort(httplib.BAD_REQUEST)

        try:
            msg = json.loads(msg)
        except ValueError as err:
            log.error('bad JSON message - %s', msg)
            self.abort(httplib.BAD_REQUEST)

        key = db.decode_key(msg['key'])
        time = datetime.strptime(msg['time'], time_fmt)
        for chan_key in msg['channels']:
            try:
                chan = db.Channel.from_key(chan_key)
                if not chan:
                    log.error('unknown channel - %s (msg key=%s)',
                              chan_key, msg['key'])
                    continue
                db.Update.create(chan, key, time)
            except db.Error as err:
                log.error('error updating %s - %s', chan, err)


class FlagTask(RequestHandler):
    def post(self):
        self.assert_internal('X-Appengine-QueueName')
        key = self.request.get('key')
        if key is None:
            log.error('no key')
            self.abort(httplib.BAD_REQUEST)

        sender = 'flag task <flagtask@{}.appspotmail.com>'.format(
            app_identity.get_application_id())
        subject = '[FLAGGED] {}'.format(key)
        body = '{} was flagged'.format(key)
        # FIXME: "to" as configuration
        mail.send_mail(sender, 'flags@insiderr.com', subject, body)


class FeedbackTask(RequestHandler):
    def post(self):
        self.assert_internal('X-Appengine-QueueName')
        user = self.request.get('user')
        content = self.request.get('content')

        if not (user and content):
            log.error('missing parameter - %s', 'content' if user else 'user')
            self.abort(httplib.BAD_REQUEST)

        sender = 'feedback task <feedbacktask@{}.appspotmail.com>'.format(
            app_identity.get_application_id())
        subject = '[{}] Feedback'.format(datetime.now())
        body = 'Feedback from {}:\n{}'.format(user, content)
        # FIXME: "to" as configuration
        mail.send_mail(sender, 'feedback@insiderr.com', subject, body)


api_prefix = '/api/v1'
routes = []
#from datetime import datetime
#d = datetime.strptime("01/03/2015", "%d/%m/%Y")
if True: #(datetime.now() < d):
    routes = [
        (api_prefix + '/register/', RegisterHandler),
        (api_prefix + '/login/', LoginHandler),

        (api_prefix + '/posts/(.*)', PostsHandler),
        (api_prefix + '/comments/(.*)', CommentsHandler),
        (api_prefix + '/votes/(.*)/(.*)', VotesHandler),
        (api_prefix + '/channels/(.*)', ChannelsHandler),
        (api_prefix + '/updates/', UpdatesHandler),
        (api_prefix + '/items/', ItemsHandler),
        (api_prefix + '/flag/(.*)', FlagHandler),
        (api_prefix + '/icons', IconsHandler),
        (api_prefix + '/feedbacks/', FeedbackHandler),

        # Tasks
        (update_task_url, UpdateTask),
        (flag_task_url, FlagTask),
        (feedback_task_url, FeedbackTask),
    ]

# FIXME: Find a better way, I hate test code going into production
if is_local_srv():
    class TestChannelHandler(RequestHandler):
        def post(self, title):
            chan = db.Channel.from_title(title)
            if not chan:
                chan = db.Channel.create(title)
            self.key_reply(chan)

    routes += [(api_prefix + '/_t/channel/(.*)', TestChannelHandler)]


app = webapp2.WSGIApplication(routes, debug=is_local_srv())
