'''
# DB Ancestry
.
|-- Channel
|-- Update
`-- User
    |-- Post
    |   |-- Comment
    |   |   |-- Flag
    |   |   |-- DownVote
    |   |   `-- UpVote
    |   |-- Flag
    |   |-- DownVote
    |   `-- UpVote
    `-- Token

# Post Users

Users in post are different from users in the database. Each post has uid_map
which maps user id to post user id. The initial user is 0. Comments and Votes
users are the post user id.

# Updates
We keep a list of updates per channel. Each update has time and object that was
changed.
'''
from google.appengine.ext import ndb

from crypt import crypt
from random import randint
import logging as log
from datetime import datetime

# Generate with crypt.mksalt(crypt.METHOD_SHA512)
_salt = '$6$/8uVjwsTUDgiFkDt'


KeyType = ndb.Key


class Error(Exception):
    pass


class NotFound(Error):
    pass


class Duplicate(Error):
    pass


def decode_key(key):
    return ndb.Key(urlsafe=key)


def decode_key_or_none(key):
    try:
        return ndb.Key(urlsafe=key)
    except:
        return None

def encode_key(key):
    return key.urlsafe()


class Model(ndb.Model):
    # TODO: Not happy that model knows about representation, think about how to
    # do this code better
    json_attrs = set()  # External JSON attributes
    json_conv = {}  # key rename

    @staticmethod
    def from_key(key):
        key = decode_key(key)
        return key.get()

    def to_dict(self, include_future=False):
        orig = super(Model, self).to_dict()

        obj = {
            self.json_conv.get(key, key): orig[key]
            for key in self.json_attrs if key in orig
        }
        obj['key'] = encode_key(self.key)
        return obj

    @classmethod
    def delete_multi(cls, ancestor_key):
        ndb.delete_multi(cls.query(ancestor=ancestor_key).iter(keys_only=True))


class Token(Model):
    '''Access tokens are stored as children of the user.

    We use the token object key value as the token, so no attributes.
    '''


def hash_pub_key(pub_key):
    return crypt(pub_key, _salt)


class User(Model):
    pub_key = ndb.StringProperty()  # Public key hash
    description = ndb.StringProperty() # Custom user description
    # List of channels this user is subscribed to
    channels = ndb.StringProperty(repeated=True)

    @staticmethod
    def from_token(token):
        key = decode_key(token)
        parent = key.parent()
        if not parent:
            log.error('token with no parent - %s', token)
            return None
        return parent.get()

    def del_tokens(self):
        # Delete old tokens
        query = Token.query(ancestor=self.key)
        ndb.delete_multi(query.iter(keys_only=True))

    @staticmethod
    def from_pub_key(pub_key, use_hash=True):
        if use_hash:
            pk_hash = hash_pub_key(pub_key)
            query = User.query(User.pub_key == pk_hash)
        else:
            query = User.query(User.pub_key == pub_key)
        return query.get()

    def login(self):
        self.del_tokens()
        token = Token(parent=self.key)
        token.put()
        return encode_key(token.key)

    @staticmethod
    def create(pub_key, description=None, use_hash=True):
        if User.from_pub_key(pub_key, use_hash=use_hash):
            raise Duplicate(pub_key)

        if use_hash:
            pk_hash = hash_pub_key(pub_key)
            user = User(pub_key=pk_hash)
        else:
            user = User(pub_key=pub_key)
        if description:
            user.description = description
        user.put()
        return user

    def uid(self):
        return encode_key(self.key)


def ilen(it):
    '''Length of iterable.

    Note: This consumes the iterable.
    '''
    return sum(1 for _ in it)


class Votable(object):
    def _votes(self, cls):
        query = cls.query(ancestor=self.key)
        return query.fetch()

    def upvotes(self):
        return self._votes(UpVote)

    def downvotes(self):
        return self._votes(DownVote)

    def to_dict(self, include_future=False):
        return {
            'upvote_count': ilen(self.upvotes()),
            'downvote_count': ilen(self.downvotes()),
        }


class Post(Model, Votable):
    '''Post, ancestor will be the user'''
    content = ndb.StringProperty()
    theme = ndb.StringProperty()
    background = ndb.StringProperty()
    # FIXME: Do we want to randomize?
    created = ndb.DateTimeProperty(auto_now_add=True)
    # List of channels this post is belong to
    channels = ndb.StringProperty(repeated=True)
    # User specified data
    role = ndb.StringProperty()
    role_text = ndb.TextProperty()

    # Map of uid -> (uid, icon id)
    uid_map = ndb.PickleProperty()

    json_attrs = set([
        'content', 'role', 'role_text', 'theme', 'background', 'channels',
        'created',
    ])

    @staticmethod
    def create(user, content, theme, background, channels, role,
               role_text, created=None):
        post_uid = 0
        uid_map = {user.uid(): post_uid}
        post = Post(
            content=content,
            theme=theme,
            background=background,
            channels=channels,
            uid_map=uid_map,
            parent=user.key,
            role=role,
            role_text=role_text,
        )
        if created:
            post.created = created
        post.put()
        return post

    def comments(self, include_future=False):
        if not include_future:
            query = Comment.query(Comment.created <= datetime.now(), ancestor=self.key).order(-Comment.created)
        else:
            query = Comment.query(ancestor=self.key).order(-Comment.created)
        return query.iter()

    def to_dict(self, include_future=False):
        obj = super(Post, self).to_dict(include_future=include_future)
        obj['comment_count'] = ilen(self.comments(include_future=include_future))
        obj.update(Votable.to_dict(self, include_future=include_future))
        return obj

    def parent_post(self):
        return self


def new_random_uid(existing):
    # We don't do "while True" here so if we get more than 1M users on a
    # post it will hang
    for i in xrange(1000):
        uid = randint(1, 1000000)
        if uid not in existing:
            break

    return uid


@ndb.transactional
def post_user(post, user):
    uid = post.uid_map.get(user.uid())
    if uid is None:
        uid = new_random_uid(set(post.uid_map.itervalues()))
        post.uid_map[user.uid()] = uid
        # TODO: Think if we want to save here or elsewhere
        post.put()

    return uid


def resolve_post_uid(post, uid):
    for k, v in post.uid_map.iteritems():
        if v == uid:
            return k


class Comment(Model, Votable):
    '''Comment, ancestor will be the post'''
    content = ndb.StringProperty()
    user = ndb.IntegerProperty()
    created = ndb.DateTimeProperty(auto_now_add=True)
    # User specified data
    role = ndb.StringProperty()
    role_text = ndb.TextProperty()

    json_attrs = set(['role', 'role_text', 'content', 'created', 'user'])
    json_conv = {'user': 'icon'}

    @staticmethod
    def create(post, user, content, role, role_text, created=None):
        uid = post_user(post, user)
        comment = Comment(
            content=content,
            user=uid,
            parent=post.key,
            role=role,
            role_text=role_text,
        )
        if created:
            comment.created = created
        comment.put()
        return comment

    def parent_post(self):
        return self.key.parent().get()

    def to_dict(self, include_future=False):
        obj = super(Comment, self).to_dict(include_future=include_future)
        obj.update(Votable.to_dict(self, include_future=include_future))
        return obj


def delete_votes(cls, ancestor, uid):
    query = cls.query(
        Vote.user == uid,
        ancestor=ancestor)
    ndb.delete_multi(query.iter(keys_only=True))


class Vote(Model):
    user = ndb.IntegerProperty()
    created = ndb.DateTimeProperty(auto_now_add=True)

    @staticmethod
    def create(obj, user, direction, delete_opposite=True):
        post = obj.parent_post()

        # FIXME: Prevent double voting
        uid = post_user(post, user)
        if direction == 'up':
            cls, del_cls = UpVote, DownVote
        else:
            cls, del_cls = DownVote, UpVote

        # make sure to delete opposite votes before voting
        if delete_opposite:
            delete_votes(del_cls, obj.key, uid)

        obj = cls(user=uid, parent=obj.key)
        obj.put()

        return obj

    @staticmethod
    def delete(obj, user, direction):
        post = obj.parent_post()

        # FIXME: Prevent double voting
        uid = post_user(post, user)
        cls = UpVote if direction == 'up' else DownVote

        delete_votes(cls, obj.key, uid)

    def parent_post(self):
        return self.key.parent().get()


class UpVote(Vote):
    pass


class DownVote(Vote):
    pass


def unique_updates(query):
    # Since we have can have multiple updates on an object and we'd like to get
    # just one update, and the fact that GQL DISTINCT is limited to full
    # projection only - we do the following
    seen = set()
    for update in query:
        if update.what in seen:
            continue

        seen.add(update.what)
        # Update are sorted by creation time reversed, so send out the first
        # one which is the freshest
        yield update


class Channel(Model):
    json_attrs = set(['title'])

    title = ndb.StringProperty()

    @staticmethod
    def create(title):
        chan = Channel(title=title)
        chan.put()
        return chan

    @staticmethod
    def from_title(title):
        query = Channel.query(Channel.title == title)
        for chan in query:
            return chan

    def find(self, since, key, count):
        chan = encode_key(self.key)
        if count > 0:
            query = Post.query(
                Post.created >= since,
                Post.created <= datetime.now(),
                Post.channels == chan
            ).order(Post.created, Post.key)
            if key:
                query.filter(ndb.GenericProperty("key") >= key)
        else:
            query = Post.query(
                Post.created <= since,
                Post.channels == chan,
            ).order(-Post.created, -Post.key)
            if key:
                query.filter(ndb.GenericProperty("key") <= key)

        return query.fetch(abs(count))

    @staticmethod
    def iter_all():
        query = Channel.query()
        return query.iter()


class Update(Model):
    '''Represents update in a channel'''
    created = ndb.DateTimeProperty(auto_now_add=True)
    what = ndb.KeyProperty()  # Changed item
    what_kind = ndb.StringProperty() # Kind of the object referenced by 'what'
    post = ndb.KeyProperty()  # Related post
    channel = ndb.KeyProperty()

    @staticmethod
    def create(chan, key, time):
        obj = key.get()
        try:
            post = obj.parent_post()
        except AttributeError:
            post = None

        update = Update(
            created=time,
            what=key,
            what_kind=key.kind(),
            post=post.key if post else None,
            channel=chan.key,
        )
        update.put()

    @staticmethod
    def updates_for(keys, since, kinds=None):
        query = Update.query(
            Update.created >= since,
            Update.created <= datetime.now(),
            Update.post.IN(keys),
        )
        if kinds:
            query = query.filter(Update.what_kind.IN(kinds))

        return unique_updates(query.order(-Update.created))


class Flag(Model):
    created = ndb.DateTimeProperty(auto_now_add=True)

    @staticmethod
    def create(key, time):
        flag = Flag(created=time, parent=key)
        flag.put()

        return flag

    @staticmethod
    def flags_for(obj):
        return Flag.query(ancestor=obj.key)


class Feedback(Model):
    created = ndb.DateTimeProperty(auto_now_add=True)
    user = ndb.KeyProperty()
    content = ndb.TextProperty()

    json_attrs = set(['created', 'user', 'content'])

    @staticmethod
    def create(user, content):
        fb = Feedback(user=user.key, content=content)
        fb.put()

        return fb


def get_multi(keys):
    keys = list(decode_key_or_none(key) for key in keys)
    # return ndb.get_multi(list(key for key in keys if key))
    futures = [key.get_async() for key in keys if key]
    results = []
    for future in futures:
        if future:
            try:
                results.append(future.get_result())
            except:
                pass
    return results
