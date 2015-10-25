'''Handle web admin console'''
# FIXME: Auth

from api import is_local_srv, jsonify
import db

from google.appengine.api import users
from google.appengine.datastore.datastore_query import Cursor
import jinja2
import webapp2

from os.path import dirname
import httplib
import json
import logging as log
from datetime import datetime

get_template = jinja2.Environment(
    loader=jinja2.FileSystemLoader(dirname(__file__))).get_template


# Default posting users for webedit
user_pub_keys = [
    'webedit-user #1',
    'webedit-user #2',
    'webedit-user #3',
    'webedit-user #4',
    'webedit-user #5',
    'webedit-user #6',
    'webedit-user #7',
    'webedit-user #8',
    'webedit-user #9',
]


def edit_users():
    keys = sorted(user_pub_keys)
    query = db.User.query(
        db.User.pub_key >= keys[0],
        db.User.pub_key <= keys[-1])
    users = query.order(db.User.pub_key).fetch(keys_only=True)
    if not users:
        for key in keys:
            users.append(db.User.create(key, description=key, use_hash=False).key)
    return users


def edit_user():
    users = edit_users()
    return users[0].get()


edit_time_fmts = ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M']

def edit_str2dt(v):
    for fmt in edit_time_fmts:
        try:
            return datetime.strptime(v, fmt)
        except:
            pass
    return None

class Editor(db.Model):
    email = db.ndb.StringProperty()


def is_editor(email):
    return Editor.query(Editor.email == email).count() > 0


def assert_editor(handler, user=None):
    user = user or users.get_current_user()
    if not (user and is_editor(user.email())):
        log.error('non editor user - %s (%s)' % (user, user.email()))
        handler.abort(httplib.UNAUTHORIZED)


class Page(webapp2.RequestHandler):
    template = None  # Defined in child

    def get(self, key=None):
        user = users.get_current_user()
        if not user:
            url = users.create_login_url(self.request.uri)
            self.redirect(url)
            return

        assert_editor(self, user)

        # FIXME: Check that user is admin
        template = get_template(self.template)
        logout = users.create_logout_url(self.request.uri)
        obj = None
        if key:
            obj = db.decode_key(key).get()
        self.response.write(template.render(
            user=user,
            edit_users=list(k.get() for k in edit_users()),
            logout=logout,
            obj=obj,
            channels=[c.to_dict() for c in db.Channel.iter_all()],
        ))


class PostsPage(Page):
    template = 'we-posts.html'


def is_flagged(post):
    return any(flag for flag in db.Flag.flags_for(post))


class JSONHandler(webapp2.RequestHandler):
    def respond(self, obj):
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(jsonify(obj))

    def get(self, key=None):
        assert_editor(self)

        cur = Cursor(urlsafe=self.request.get('cur'))
        count = int(self.request.get('count', 500))
        objs, cur, more = self.query(key).fetch_page(count, start_cursor=cur)
        user_key_to_desc = dict((key, key.get().pub_key) for key in edit_users())
        resp = {
            'items': [self.to_dict(obj, user_key_to_desc) for obj in objs],
            'cur': cur.urlsafe() if cur else None,
            'more': more,
        }
        self.respond(resp)

    def to_dict(self, obj, user_key_to_desc):
        return obj.to_dict(include_future=True)

    def delete_related_objects(self, key):
        db.Update.delete_multi(ancestor_key=key)
        db.UpVote.delete_multi(ancestor_key=key)
        db.DownVote.delete_multi(ancestor_key=key)

    def delete(self, key=None):
        assert_editor(self)

        if not key:
            log.error('missing key')
            self.abort(httplib.BAD_REQUEST)

        try:
            key = db.decode_key(key)
        except TypeError as err:
            log.error('bad key %s', err)
            self.abort(httplib.BAD_REQUEST)

        self.delete_related_objects(key)

        key.delete()
        self.respond({'ok': True})

    def gen_votes(self, obj, user, data):
        for i in range(int(data['upvote_count'])):
            db.Vote.create(obj, user, 'up', delete_opposite=False)
        for i in range(int(data['downvote_count'])):
            db.Vote.create(obj, user, 'down', delete_opposite=False)

    def update_votes_cls(self, obj, user, count, cls, uid=None):
        post = obj.parent_post()
        uid = uid or db.post_user(obj.parent_post(), user)
        query = cls.query(cls.user == uid, ancestor=obj.key)
        existing = query.count()
        direction = 'up' if cls == db.UpVote else 'down'
        if existing < count:
            for i in range(count - existing):
                db.Vote.create(obj, user, direction, delete_opposite=False)
        elif existing > count:
            db.ndb.delete_multi(query.fetch(keys_only=True, limit=(existing - count)))

    def update_votes(self, obj, user, data, uid=None):
        self.update_votes_cls(obj, user, int(data['upvote_count']), db.UpVote, uid)
        self.update_votes_cls(obj, user, int(data['downvote_count']), db.DownVote, uid)

    def query(self, key):
        pass


class JSPosts(JSONHandler):
    def to_dict(self, post, user_key_to_desc):
        d = super(JSPosts, self).to_dict(post, user_key_to_desc)
        user_key = post.key.parent()
        d['user'] = user_key_to_desc.get(user_key, user_key.urlsafe())
        d['flagged'] = is_flagged(post)
        return d

    def query(self, key):
        return db.Post.query().order(-db.Post.created)

    def delete_related_objects(self, key):
        db.Comment.delete_multi(ancestor_key=key)

    def post(self, ignored=None):
        assert_editor(self)

        user = edit_user()
        try:
            data = json.loads(self.request.body)
        except (ValueError, TypeError) as err:
            log.error('bad post - %s', err)
            self.abort(httplib.BAD_REQUEST)

        channel = data.get('channel', [])
        if channel:
            channels = [channel]
        else:
            channel = db.Channel.query().get()
            if channel:
                channels = [db.encode_key(channel.key)]

        created = data.get('created', None)
        if created:
            created = edit_str2dt(created)

        try:
            post = db.Post.create(
                user,
                data['content'],
                data['theme'],
                data['background'],
                channels,
                data['role'],
                data['role_text'],
                created=created,
            )
        except KeyError as err:
            log.error('missing field - %s', err)
            self.abort(httplib.BAD_REQUEST)

        self.gen_votes(post, user, data)
        self.respond({'ok': True, 'key': db.encode_key(post.key)})

    def put(self, key=None):
        assert_editor(self)

        if not key:
            log.error('missing key')
            self.abort(httplib.BAD_REQUEST)

        try:
            data = json.loads(self.request.body)
        except (ValueError, TypeError) as err:
            log.error('bad post - %s', err)
            self.abort(httplib.BAD_REQUEST)

        try:
            post = db.Post.from_key(key)
        except db.NotFound:
            log.error('unknown post - %s', key)
            self.abort(httplib.NOT_FOUND)

        created = data.get('created', None)
        if created:
            created = edit_str2dt(created)

        self.update_votes(post, post.key.parent().get(), data)

        post.content = data['content']
        post.theme = data['theme']
        post.background = data['background']
        post.role = data['role']
        post.role_text = data['role_text']
        if created:
            post.created = created
        post.put()
        self.respond({'ok': True})


class CommentsPage(Page):
    template = 'we-comments.html'


class JSComments(JSONHandler):
    def query(self, key):
        key = db.decode_key(key)
        return db.Comment.query(ancestor=key).order(-db.Comment.created)

    def to_dict(self, obj, user_key_to_desc):
        d = super(JSComments, self).to_dict(obj, user_key_to_desc)
        user_key = db.resolve_post_uid(obj.parent_post(), obj.user)
        if user_key:
            d['user'] = user_key_to_desc.get(db.decode_key(user_key), user_key)
        else:
            d['user'] = 'unknown'
        return d

    def post(self, key=None):
        assert_editor(self)
        if not key:
            log.error('missing key')
            self.abort(httplib.BAD_REQUEST)

        try:
            data = json.loads(self.request.body)
        except (ValueError, TypeError) as err:
            log.error('bad comment - %s', err)
            self.abort(httplib.BAD_REQUEST)

        created = data.get('created', None)
        if created:
            created = edit_str2dt(created)

        euser = None
        user = data.get('user', None)
        for u in edit_users():
            if db.encode_key(u) == user:
                euser = u.get()
                break
        if not euser:
            log.error('bad user (not edit user) - %s', user)
            self.abort(httplib.BAD_REQUEST)

        post = db.decode_key(key).get()
        try:
            comment = db.Comment.create(
                post,
                euser,
                data['content'],
                data['role'],
                data['role_text'],
                created,
            )
        except KeyError as err:
            log.error('missing field - %s', err)
            self.abort(httplib.BAD_REQUEST)

        self.gen_votes(comment, euser, data)
        self.respond({'ok': True, 'key': db.encode_key(comment.key)})

    def put(self, key=None):
        assert_editor(self)

        if not key:
            log.error('missing key')
            self.abort(httplib.BAD_REQUEST)

        try:
            data = json.loads(self.request.body)
        except (ValueError, TypeError) as err:
            log.error('bad put - %s', err)
            self.abort(httplib.BAD_REQUEST)

        try:
            comment = db.Comment.from_key(key)
        except db.NotFound:
            log.error('unknown comment - %s', key)
            self.abort(httplib.NOT_FOUND)

        created = data.get('created', None)
        if created:
            created = edit_str2dt(created)

        post = comment.parent_post()

        user = db.resolve_post_uid(comment.parent_post(), comment.user)
        if user:
            user = db.User.from_key(user)
        self.update_votes(comment, user, data, uid=comment.user)

        comment.content = data['content']
        comment.role = data['role']
        comment.role_text = data['role_text']
        if created:
            comment.created = created
        comment.put()
        self.respond({'ok': True})


editors = [
    'someone@gmail.com',
]

class InitHandler(webapp2.RequestHandler):
    def post(self):
        count = 0
        for email in editors:
            if is_editor(email):
                continue
            count += 1
            e = Editor(email=email)
            e.put()

        reply = {'ok': True, 'added': count}
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(jsonify(reply))


route_prefix = '/_we'
routes = []
#from datetime import datetime
#d = datetime.strptime("01/03/2015", "%d/%m/%Y")
if True: #(datetime.now() < d):
    routes = [
        (route_prefix + '/', PostsPage),
        (route_prefix + '/js/posts/(.*)', JSPosts),
        (route_prefix + '/comments/(.*)', CommentsPage),
        (route_prefix + '/js/comments/(.*)', JSComments),
        (route_prefix + '/init', InitHandler),
    ]

app = webapp2.WSGIApplication(routes, debug=is_local_srv())
