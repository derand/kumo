#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# kumo
# an imageboard for Google App Engine
#
# tslocum@gmail.com
# https://github.com/tslocum/kumo

__author__ = 'Trevor Slocum'
__license__ = 'GNU GPL v3'

MAX_THREADS = 50
THREADS_SHOWN_ON_FRONT_PAGE = 10
REPLIES_SHOWN_ON_FRONT_PAGE = 5
SECONDS_BETWEEN_NEW_THREADS = 30
SECONDS_BETWEEN_REPLIES = 10

MAX_IMAGE_SIZE_BYTES = 1048576
MAX_IMAGE_SIZE_DISPLAY = '1 MB'
MAX_DIMENSION_FOR_OP_IMAGE = 200
MAX_DIMENSION_FOR_REPLY_IMAGE = 125
MAX_DIMENSION_FOR_IMAGE_CATALOG = 50

ANONYMOUS = 'Anonymous'
POST_TRIPCODE_CHARACTER = '!'

import StringIO
import sys
import string
import cgi
import wsgiref.handlers
import Cookie
import os
import time
import datetime
import re
import zlib
import gettext
import struct
import math
import urllib
import logging

# Set to true if we want to have our webapp print stack traces, etc
_DEBUG = False

import crypt
from crypt import crypt

from google.appengine.api import users
from google.appengine.api import urlfetch
from google.appengine.api import images
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext.webapp import template
from google.appengine.ext import db
from google.appengine.ext import search

from time import strftime
from datetime import datetime
from hashlib import md5
from hashlib import sha224
from StringIO import StringIO


# Wishful thinking
gettext.bindtextdomain('kumo')
gettext.textdomain('kumo')
_ = gettext.gettext

# If this is set to True, special administrator commands will be visible, and pages will not be retrieved from/stored in the cache
_ADMINISTRATOR_VIEW = False

# Begin Datastore models

# Page cache
class Page(db.Model):
  identifier = db.StringProperty() # Board front pages are stored as 'front' for page 0, and 'front<n>' for each page thereafter.  Res pages are stored as the thread OP's datstore key
  contents = db.BlobProperty()

# Google account tracking and preferences
class UserPrefs(db.Model):
  user = db.UserProperty() # User object
  level = db.CategoryProperty(default='Normal') # TODO: Access level
  name_override = db.StringProperty(default='')
  posts = db.IntegerProperty(default=0) # Number of posts this user has made
  posts_sage = db.IntegerProperty(default=0) # Number of sage posts this user has made

class Ban(db.Model):
  ip = db.StringProperty()
  reason = db.StringProperty()
  placed = db.DateTimeProperty()

class Image(db.Model):
  data = db.BlobProperty()
  thumb_data = db.BlobProperty()
  thumb_catalog_data = db.BlobProperty()
  attachedpost = db.StringProperty()

class Post(search.SearchableModel):
  postid = db.IntegerProperty() # Consecutive ID
  parentid = db.IntegerProperty() # Parent of post, <None> if this post has no parent
  author = db.UserProperty() # Google account used when making the post
  posts = db.IntegerProperty() # Number of posts in a thread (this includes the OP)
  image = db.StringProperty() # File attachment to post, stored here as a key to the actual instance, lowering memory when only fetching posts
  image_filename = db.StringProperty() # Full filename of the stored file ([timestamp + postid].ext)
  image_hex = db.StringProperty() # File hex for quick duplicate checks
  image_mime = db.StringProperty() # File MIME
  image_extension = db.StringProperty() # File extension (including period)
  image_original = db.StringProperty() # Original filename when the file was uploaded
  image_size = db.IntegerProperty() # Size of the file in bytes
  image_size_formatted = db.StringProperty() # Formatted version of size (KB)
  image_width = db.IntegerProperty() # Width of image
  image_height = db.IntegerProperty() # Height of image
  thumb_filename = db.StringProperty() # Full filename of the stored file thumbnail ([timestamp + postid]s.ext)
  thumb_width = db.IntegerProperty() # Width of thumbnail
  thumb_height = db.IntegerProperty() #height of thumbnail
  thumb_catalog_width = db.IntegerProperty() # Width of thumbnail (catalog)
  thumb_catalog_height = db.IntegerProperty() #height of thumbnail (catalog)
  ip = db.StringProperty() # IP address used when making the post
  nameblock = db.StringProperty() # Preprocessed HTML block containing the name, tripcode, and email of the post
  anonymous = db.BooleanProperty() # Flag regarding if the poster's Google account should be displayed in the post, however it can also remove anything entered in the Name field
  name = db.StringProperty()
  tripcode = db.StringProperty()
  email = db.StringProperty()
  subject = db.StringProperty()
  message = db.TextProperty()
  password = db.StringProperty() # Stored as a hash
  date = db.DateTimeProperty(auto_now_add=True) # Datetime of when post was made
  date_formatted = db.StringProperty() # Preprocessed formatting of the datetime
  bumped = db.DateTimeProperty() # Datetime of when the post was bumped
  deleted = db.BooleanProperty()
  
class Idx(db.Model):
  name = db.StringProperty(required=True)
  count = db.IntegerProperty(required=True)

class Ban(db.Model):
  ip = db.StringProperty()
  reason = db.StringProperty()
  placed = db.DateTimeProperty()

# End Datastore models

class Thread():
  relid = 0
  navlinks = ''
  posts = ''
  replieshidden = 0
  op_postid = 0
  op_key = None
  posts_in_thread = 0

class BaseRequestHandler(webapp.RequestHandler):
  """Supplies a common template generation function.

  When you call generate(), we augment the template variables supplied with
  the current user in the 'user' variable and the current webapp request
  in the 'request' variable.
  """
  def generate(self, template_name, template_values={}, doreturn=False):
    global _ADMINISTRATOR_VIEW
    
    account_greeting = ''
    loggedin = False
    
    user = users.get_current_user()
    if user:
      loggedin = True
      account_greeting = 'Welcome, ' + user.nickname() + '. '
      url = users.create_logout_url(self.request.uri)
      url_linktext = _('Log out')
    else:
      account_greeting = 'You may post anonymously, or '
      url = users.create_login_url(self.request.uri)
      url_linktext = _('Log in')
      
    values = {
      'request': self.request,
      'user': users.GetCurrentUser(),
      'account_url_pretext': account_greeting,
      'account_url': url,
      'account_url_linktext': url_linktext,
      'loggedin': loggedin,
      'administrator': users.is_current_user_admin(),
      'administratorview': _ADMINISTRATOR_VIEW,
      'title': 'kumo',
      'application_name': 'kumo',
      'is_page': 'false',
      'MAX_FILE_SIZE': MAX_IMAGE_SIZE_BYTES,
      'maxsize_display': MAX_IMAGE_SIZE_DISPLAY,
      'maxdimensions': MAX_DIMENSION_FOR_OP_IMAGE,
    }
    values.update(template_values)
    
    directory = os.path.dirname(__file__)
    path = os.path.join(directory, os.path.join('templates', template_name))
    
    if doreturn:
      return template.render(path, values, debug=_DEBUG)
    else:
      self.response.out.write(template.render(path, values, debug=_DEBUG))

  """Simple self.redirect() replacement.  Using the command provided in the SDK yields ugly refreshes."""
  def redirect_meta(self, destination):
    self.response.out.write('<meta http-equiv="refresh" content="0;url=' + destination + '">--&gt; --&gt; --&gt;')
  
  def error(self, error):
    template_values = {
      'error': error,
      }
    self.generate('error.html', template_values)

class ViewImage(BaseRequestHandler):
  def get(self, postkey, dummy_filename):
    post = Post.get(postkey)
    if post:
      displayImage(self, post.image, post.image_mime)
    else:
      self.response.out.write('Could not find file %s.' % dummy_filename)

class ViewImageThumb(BaseRequestHandler):
  def get(self, postkey, dummy_filename):
    post = Post.get(postkey)
    # and post.image_filename == dummy_filename
    if post:
      displayImage(self, post.image, post.image_mime, True)
    else:
      self.response.out.write('Could not find file %s.' % dummy_filename)

class ViewImageThumbCat(BaseRequestHandler):
  def get(self, postkey, dummy_filename):
    post = Post.get(postkey)
    # and post.image_filename == dummy_filename
    if post:
      displayImage(self, post.image, post.image_mime, False, True)
    else:
      self.response.out.write('Could not find file %s.' % dummy_filename)
      
class FinishImage(BaseRequestHandler):
  def post(self):
    finish_id = self.request.POST['id']
    file_data = self.request.POST['file'].file.read()

    image = getImage(finish_id)
    if image:
      if not image.thumb_data:
        image.thumb_data = file_data
        image.put()

class FinishImageCat(BaseRequestHandler):
  def post(self):
    finish_id = self.request.POST['id']
    file_data = self.request.POST['file'].file.read()

    image = getImage(finish_id)
    if image:
      if not image.thumb_catalog_data:
        image.thumb_catalog_data = file_data
        image.put()
      
class MainPage(BaseRequestHandler):
  def get(self, pagenum=0):
    pagenum = int(pagenum)
    if pagenum >= 0 and pagenum <= 9:
      try:
        fetchpage(self, 'front', pagenum)
      except:
        return self.error('Error: Application quota exceeded.  Please wait a moment and try again.')
    else:
      return self.error('Invalid page number.')

class ResPage(BaseRequestHandler):
  def get(self, thread, action=None):
    thread = str(postIDToKey(thread))
    if not action:
      fetchpage(self, thread)
    else:
      threads = getposts(self, thread, 0, action)
      writepage(self, threads, thread)

class Catalog(BaseRequestHandler):
  def get(self):
    page = Page.all().filter('identifier = ', 'catalog').get()
    if not page:
      page = Page(identifier='catalog')

      threads = Post.all().filter('parentid = ', None).order('-bumped')
      posts = []
      rows = []
      
      i = 0
      for thread in threads:
        posts.append(thread)
        i += 1
        if i == 12:
          i = 0
          rows.append(posts)
          posts = []
      # If there are more posts which were not placed in their own row
      if posts != []:
        # Add them to another row
        rows.append(posts)
      
      template_values = {
        'rows': rows,
        'catalogdimensions': MAX_DIMENSION_FOR_IMAGE_CATALOG,
      }
      
      page.contents = self.generate('catalog.html', template_values, True)
      page.put()

    self.response.out.write(page.contents)

class Panel(BaseRequestHandler):
  def get(self):
    template_values = {}
    
    if users.get_current_user():
      current_user = users.get_current_user()
      user_prefs = UserPrefs().all().filter('user = ', current_user)
      user_prefs = user_prefs.get()
      template_values = {
        'user_prefs': user_prefs,
      }
    
    self.generate('panel.html', template_values)

class Search(BaseRequestHandler):
  def get(self):
    results = None
    numresults = None
    query = self.request.get('query', '')
    
    if query != '':
      results = Post.all().search(query)
      if results:
        numresults = results.count()
        if numresults == 0:
          numresults = None
                                 
    template_values = {
      'query': query,
      'results': results,
      'numresults': numresults,
    }
    
    self.generate('search.html', template_values)    

class Sitemap(BaseRequestHandler):
  def get(self):
    urls = []
    
    posts = Post.all().filter('parentid = ', None).order('-bumped')
                                 
    template_values = {
      'posts': posts,
    }

    self.response.headers['Content-Type'] = 'text/xml'
    self.generate('sitemap.xml', template_values)
    
class Board(BaseRequestHandler):
  def post(self):
    parent_post = None
    
    if not checkNotBanned(self, self.request.remote_addr):
      return self.error('You are banned.')
    
    request_parent = self.request.get('parent')
    if request_parent:
      post_ancestor = Post.get(request_parent)

      # Make sure we are replying to a post starting a thread, not a reply
      if post_ancestor:
        if post_ancestor.parent is not None:
          parent_post = post_ancestor
        else:
          return self.error('Error: That post is a reply.')
      else:
        return self.error('Error: Unable to locate thread.')
        
    if parent_post:
       post = Post(parent=parent_post)
       post.parentid = parent_post.postid
       post.posts = None
    else:
       post = Post()
       post.parentid = None
       post.posts = 1
    
    post.name = cgi.escape(self.request.get('name')).strip()
    name_match = re.compile(r'(.*)#(.*)').match(post.name)
    if name_match:
      if name_match.group(2):
        post.name = name_match.group(1)
        post.tripcode =  tripcode(name_match.group(2))
        
    post.email = cgi.escape(self.request.get('email')).strip()
    post.subject = cgi.escape(self.request.get('subject')).strip()
    post.message = clickableURLs(cgi.escape(self.request.get('message')).strip()[0:1000])
    post.password = cgi.escape(self.request.get('password')).strip()

    # Set cookies for auto-fill
    cookie = Cookie.SimpleCookie(self.request.headers.get('Cookie'))
    cookie['kumo_name'] = self.request.get('name')
    if post.email.lower() != 'sage' and post.email.lower() != 'age':
      cookie['kumo_email'] = post.email
    cookie['kumo_password'] = post.password
    self.response.headers['Set-cookie'] = str(cookie)
    
    post.ip = str(self.request.remote_addr)
    if self.request.get('anonymous', None) is not None:
      post.anonymous = True
    else:
      post.anonymous = False

    thefile = self.request.get('file', None)

    image_data = None
    if thefile is not None:
      image_data = db.Blob(str(thefile))
      
    if image_data:
      image = Image(data=image_data)
      try:
        image.put()
      except:
        return self.error('Error: That file is too large.')
      post.image = str(image.key())
      imageinfo = getImageInfo(image_data)
      if imageinfo[0] in ('image/gif', 'image/png', 'image/jpeg'):
        if imageinfo[1] > 0 and imageinfo[2] > 0:
          is_not_duplicate = checkImageNotDuplicate(image_data)
          if is_not_duplicate[0]:
            post.image_original = cgi.escape(self.request.params.get('file').filename).strip()
            post.image_mime = imageinfo[0]
            
            post.image_size = len(image_data)
            post.image_size_formatted = str(long(post.image_size / 1024)) + 'KB'

            if post.image_size > MAX_IMAGE_SIZE_BYTES:
              return self.error('Error: That file is too large.')
            
            if post.image_mime == 'image/gif':
              post.image_extension = '.gif'
            else:
              if post.image_mime == 'image/png':
                post.image_extension = '.png'
              else:
                if post.image_mime == 'image/jpeg':
                  post.image_extension = '.jpg'
          
            post.image_width = imageinfo[1]
            post.image_height = imageinfo[2]
            
            if parent_post:
              maxsize = MAX_DIMENSION_FOR_REPLY_IMAGE
            else:
              maxsize = MAX_DIMENSION_FOR_OP_IMAGE

            # Calculate the dimensions for the thumbnail for /thumb/
            post.thumb_width, post.thumb_height = getThumbDimensions(post.image_width, post.image_height, maxsize)

            # Calculate the dimensions for the thumbnail for /cat/
            post.thumb_catalog_width, post.thumb_catalog_height = getThumbDimensions(post.image_width, post.image_height, MAX_DIMENSION_FOR_IMAGE_CATALOG)

            post.image_hex = sha224(image_data).hexdigest()
          else:
            return self.error('Error: That image has already been posted <a href="' + threadURL(is_not_duplicate[1]) + '#' + str(is_not_duplicate[2]) + '">here</a>.')
        else:
          return self.error('Error: Unable to read image dimensions.')
      else:
        return self.error('Error:Only GIF, JPG, and PNG files are supported.')
      
    if users.get_current_user():
      current_user = users.get_current_user()
      user_prefs = UserPrefs().all().filter('user = ', current_user)
      user_prefs = user_prefs.get()
      if not user_prefs:
        user_prefs = UserPrefs(user=current_user)
      
      post.author = current_user
      post.name = ''

    if not post.image:
      if not parent_post:
        return self.error('Error: Please upload an image to start a thread.')
      if post.message == '':
        return self.error('Error: Please input a message, and/or upload an image.')

    if not checkNotFlooding(self, (post.parentid is not None)):
      return self.error('Error: Flood detected.')
      
    if post.password:
      post.password = sha224(post.password).hexdigest()
    else:
      post.password = ''

    post.nameblock = nameBlock(post)
      
    post.deleted = False

    if not parent_post:
      post.bumped = datetime.now()

    post.date_formatted = post.date.strftime("%y/%m/%d(%a)%H:%M:%S")
    
    try:
      post.postid = Counter('Post_ID').inc()
      if parent_post:
        post.message = checkRefLinks(post.message, parent_post.postid)
      else:
        post.message = checkRefLinks(post.message, post.postid)
      post.message = checkQuotes(post.message)
      post.message = checkAllowedHTML(post.message)
      post.message = post.message.replace("\n", '<br>')

      if post.image:
        image.thumb_data = images.resize(image_data, post.thumb_width, post.thumb_height)
        image.thumb_catalog_data = images.resize(image_data, post.thumb_catalog_width, post.thumb_catalog_height)
        post.image_filename = str(int(time.mktime(post.date.timetuple()))) + str(post.postid)
        post.thumb_filename = post.image_filename + 's' + post.image_extension

      post.put()
      if post.image:
        image.attachedpost = str(post.key())
        image.put()
        post.put()
    except:
      if post.image:
        image.delete()
      return self.error('Error: Unable to store post in datastore.')

    if users.get_current_user():
      user_prefs.posts += 1
      if post.email.lower() == 'sage':
        user_prefs.posts_sage += 1
      user_prefs.put()
    
    if parent_post:
      parent_post.posts += 1
      if post.email.lower() != 'sage':
        parent_post.bumped = datetime.now()
      parent_post.put()
      
      threadupdated(self, request_parent)
    else:
      trimThreads(self)
      
      threadupdated(self, None)
      
    if parent_post:
      self.redirect_meta(threadURL(parent_post.key()))
      #self.redirect('/res/' + str(parent_post.key()) + '/l50')
    else:
      self.redirect_meta('/')

class Delete(BaseRequestHandler):
  def post(self):
    password = self.request.get('password', None)

    if not checkNotBanned(self, self.request.remote_addr):
      return self.error('You are banned.')

    if password != '':
      delete = self.request.get('delete', None)
      if delete:
        post = Post.get(delete)

        if post:
          if post.password == sha224(password).hexdigest():
            imageonly = self.request.get('imageonly', None)
            
            if imageonly:
              if post.image:
                if post.parentid:
                  image = Image.get(post.image)
                  if image:
                    image.delete()
                  post.image = None
                  post.image_hex = None
                  if post.message == '':
                    post.deleted = True
                  post.put()
                else:
                  return self.error('Images can not be deleted from the first post in threads.')
              else:
                return self.error('That post does not have an image attached to it.')
            else:
              deletePost(self, post)

            if post.parentid:
              threadupdated(self, post.parent().key())
              self.redirect_meta(threadURL(post.parent().key()))
            elif not imageonly:
              self.redirect_meta('/')
            else:
              threadupdated(self, post.key())
              self.redirect_meta(threadURL(post.key()))
          else:
            return self.error('Error: Incorrect password.')
        else:
          return self.error('Error: Please check a box next to a post.')
      else:
        return self.error('Error: Please check a box next to a post.')
    else:
      return self.error('Error: Please enter a password.')
    
class AdminPage(BaseRequestHandler):
  def get(self,arg1=None,arg2=None,arg3=None):
    global _ADMINISTRATOR_VIEW
    page_text = ''
    
    if not users.is_current_user_admin():
      return self.error('You are not authorized to view this page.')

    _ADMINISTRATOR_VIEW = True
    
    if arg1:
      if arg1 == 'delete':
        post = Post.get(arg2)
        if post:
          deletePost(self, post)
          page_text += 'Post removed'
        else:
          page_text += 'Error: Post not found'
      elif arg1 == 'ban':
        post = Post.get(arg2)
        if post:
          if checkNotBanned(self, post.ip):
            ban = Ban()
            ban.ip = post.ip
            ban.placed = datetime.now()
            ban.put()
            page_text += 'Ban placed.'
          else:
            page_text += 'That user is already banned.'
        else:
          page_text += 'Error: Post not found'
      elif arg1 == 'clearcache':
        pages = Page.all()
        i = 0
        for page in pages:
          page.delete()
          i += 1
        page_text += 'Page cache cleared: ' + str(i) + ' pages deleted'
    
    template_values = {
      'title': 'kumo management panel',
      'page_text': page_text,
      'administrator': users.is_current_user_admin(),
    }
    
    self.generate('panel_admin.html', template_values)

def getposts(self, thread_op=None,startat=0,special=None):
  global total_threads
  threads = []
  posts = []
  
  if thread_op:
    thread_entity = Post.get(thread_op)
    if not thread_entity:
      raise
    thread = Post.all().ancestor(thread_entity).order('date')
    firstid = 1
    offset = 0

    if special and special != '':
      if special == 'l50':
        replies_total = db.GqlQuery("SELECT * FROM Post WHERE ANCESTOR IS :1", thread_entity)
        numposts = replies_total.count()
        offset = max(0, (numposts - 50))
        thread = thread.fetch(50, offset)
        firstid = (offset + 1)
      else:
        if special == '-100':
          thread = thread.fetch(100)
        #else:
        #  raise Exception, 'Invalid operation supplied through URL.'

    if offset > 0:
      thread_entity.relid = 1
      posts.append(thread_entity)

    # Iterate through the posts and give them their relative IDs
    i = firstid
    for post in thread:
      post.relid = i
      posts.append(post)
      i += 1

    fullthread = Thread()
    fullthread.posts = posts
    fullthread.op_postid = thread_entity.postid
    fullthread.op_key = thread_entity.key()
    
    threads.append(fullthread)
      
  else:
    total_threads = Post.all().filter('parentid = ', None)
    total_threads = total_threads.count()

    op_posts = Post.all().filter('parentid = ', None).order('-bumped')
    op_posts = op_posts.fetch(THREADS_SHOWN_ON_FRONT_PAGE, startat)

    thread_relid = 1
    for thread_parent in op_posts:
      fullthread = Thread()
      
      posts_total = db.GqlQuery("SELECT * FROM Post WHERE ANCESTOR IS :1", thread_parent)
      numposts = posts_total.count()
      fullthread.posts_in_thread = numposts
  
      offset = max((numposts - REPLIES_SHOWN_ON_FRONT_PAGE), 0)
      if offset > 0:
        # Because we aren't showing all of the posts, we need to display the original post first
        thread_starting_post = Post.all().ancestor(thread_parent).order('date')
        thread_starting_post = thread_starting_post.get()
        thread_starting_post.relid = 1
        posts.append(thread_starting_post)
        fullthread.op_postid = thread_starting_post.postid
        fullthread.op_key = thread_starting_post.key()
        
      thread = db.GqlQuery("SELECT * FROM Post WHERE ANCESTOR IS :1 "
                           "ORDER BY date ASC "
                           "LIMIT " + str(offset) + "," + str(REPLIES_SHOWN_ON_FRONT_PAGE), thread_parent)

      # Iterate through the posts and give them their relative IDs
      i = (offset + 1)
      n = 1
      for post in thread:
        if i == 1:
          fullthread.op_postid = post.postid
          fullthread.op_key = post.key()
        post.relid = i
        posts.append(post)
        i += 1
        
      fullthread.posts = posts
      fullthread.relid = thread_relid
      if thread_relid == 1:
        fullthread.navlinks = '<a href="#15">&uarr;</a>&nbsp;<a href="#2">&darr;</a>'
      else:
        if thread_relid == 15:
          fullthread.navlinks = '<a href="#14">&uarr;</a>&nbsp;<a href="#1">&darr;</a>'
        else:
          fullthread.navlinks = '<a href="#' + str(thread_relid - 1) + '">&uarr;</a>&nbsp;<a href="#' + str(thread_relid + 1) + '">&darr;</a>'
          
      if numposts > 1:
        replieshidden = ((numposts - 1) - min(thread.count(), REPLIES_SHOWN_ON_FRONT_PAGE))
        if replieshidden > 0:
          fullthread.replieshidden = replieshidden
      else:
        fullthread.replieshidden = None
      
      threads.append(fullthread)
      posts = []
      thread_relid += 1
  
  return threads

def writepage(self, threads, reply_to=None, doreturn=False, cached=False, pagenum=None):
  global time_start, total_threads
  
  execution_time = (datetime.now() - time_start)
  pages_text = ''
  cachedat = None
  
  if reply_to is None:
    pages_text = pages_text + '<td>'

    pages = int(math.ceil(float(total_threads) / 10))
    
    if pagenum == 0:
      pages_text = pages_text + 'Previous'
    else:
      previous = str(pagenum - 1)
      if previous == '0':
        previous = ''
      else:
        previous = previous + '.html'
      pages_text = pages_text + '<form method="get" action="/' + previous + '"><input value="Previous" type="submit"></form>'

    pages_text += '</td><td>'

    for i in xrange(0, pages):
      if i == pagenum:
        pages_text = pages_text + '[' + str(i) + '] '
      else:
        if i == 0:
          pages_text = pages_text + '[<a href="/">' + str(i) + '</a>] '
        else:
          pages_text = pages_text + '[<a href="/' + str(i) + '.html">' + str(i) + '</a>] '
        
    pages_text = pages_text + '</td><td>'

    next = (pagenum + 1)
    if next == 10 or next == pages:
      pages_text = pages_text + 'Next</td>'
    else:
      pages_text = pages_text + '<form method="get" action="/' + str(next) + '.html"><input value="Next" type="submit"></form></td>'

  if cached:
    cachedat = datetime.now().strftime("%y/%m/%d %H:%M:%S")

  if reply_to is None:
    ispage = 'true'
  else:
    ispage = 'false'
  template_values = {
    'threads': threads,
    'replythread': reply_to,
    'pages': pages_text,
    'execution_time_seconds': execution_time.seconds,
    'execution_time_microseconds': (execution_time.microseconds / 1000),
    'cached': cachedat,
    'is_page': ispage,
    }
  
  return self.generate('index.html', template_values, doreturn)

def fetchpage(self, page_name, pagenum=0):
  global _ADMINISTRATOR_VIEW
  
  # If ?admin is in the URL, and the current user is an administrator, don't give them a cached page
  if users.is_current_user_admin() and self.request.get('admin', None) is not None:
    _ADMINISTRATOR_VIEW = True
    
  if page_name == 'front':
    if pagenum > 0:
      page_name += str(pagenum)

    page = Page.all().filter('identifier = ', page_name).get()
      
    if page and not _ADMINISTRATOR_VIEW:
      page_contents = page.contents
    else:
      try:
        page_contents = recachepage(self, 'front', pagenum)
      except:
        return self.error('Invalid thread ID supplied.')
  else:
    page = Page.all().filter('identifier = ', page_name).get()
    if page and not _ADMINISTRATOR_VIEW:
      page_contents = page.contents
    else:
      try:
        page_contents = recachepage(self, page_name)
      except:
        return self.error('Invalid thread ID supplied.')

  self.response.out.write(page_contents)

def recachepage(self, page_name, pagenum=0):
  global _ADMINISTRATOR_VIEW
  
  if page_name == 'front':
    if pagenum > 0:
      page_name += str(pagenum)
      
    page = Page.all().filter('identifier = ', page_name).get()
    if not page:
      page = Page(identifier=page_name)

    try:
      threads = getposts(self, None, (pagenum * 10))
    except:
      raise
    page_contents = writepage(self, threads, None, True, True, pagenum)
  else:
    post = Post.get(page_name)
    if post:
      if post.parentid is not None:
        return self.error('Error: Invalid thread ID.')
    
    page = Page.all().filter('identifier = ', page_name).get()
    if not page:
      page = Page(identifier=page_name)
    
    threads = getposts(self, page_name, 0)
    page_contents = writepage(self, threads, page_name, True, True)

  if not _ADMINISTRATOR_VIEW:
    page.contents = page_contents
    page.put()

  return page_contents

def threadupdated(self, thread_key=None):
  thread_key = str(thread_key)
  logging.debug('THREADUPDATED: Thread ' + thread_key + ' updated')
  
  clearfrontpages(self)
  if thread_key:
    clearpage(self, thread_key)

def clearpage(self, page_name):
  page_name = str(page_name)
  logging.debug('CLEARPAGE: Clearing page ' + page_name)
  
  page = Page.all().filter('identifier = ', page_name).get()
  if page:
    page.delete()
    
    logging.debug('CLEARPAGE: Cleared page ' + page_name)

def clearfrontpages(self):
  clearpage(self, 'catalog')
  clearpage(self, 'front')
  for i in range(1, 10):
    clearpage(self, ('front' + str(i)))
    
def tripcode(pw):
    pw = pw.encode('sjis', 'ignore')	\
        .replace('"', '&quot;')		\
        .replace("'", '\'')		\
        .replace('<', '&lt;')		\
        .replace('>', '&gt;')		\
        .replace(',', ',')
    salt = re.sub(r'[^\.-z]', '.', (pw + 'H..')[1:3])
    salt = salt.translate(string.maketrans(r':;=?@[\]^_`', 'ABDFGabcdef'))
    
    return crypt(pw, salt)[-10:]

def deletePost(self, post):
  if post.parentid is None:
    posts = Post.all().ancestor(post)
    for a_post in posts:
      if a_post.image:
        image = Image.get(a_post.image)
        image.delete()
      a_post.delete()
  else:
    if post.image:
      image = Image.get(post.image)
      image.delete()
    post.image = None
    post.image_hex = None
    post.deleted = True
    post.put()

  # Because we deleted a post, notify the script we have updated a thread
  threadupdated(self, post.key())

def postIDToKey(postid):
  post = Post.all().filter('postid = ', int(postid)).get()
  return post.key()

def postKeyToID(key):
  post = Post.get(key)
  return post.postid

def threadURL(key):
  return '/res/' + str(postKeyToID(key)) + '.html'

def trimThreads(self):
  posts = Post.all().filter('parentid = ', None).order('-bumped')
  posts = posts.fetch(10, MAX_THREADS)

  for post in posts:
    deletePost(self, post)

def nameBlock(post):
  nameblock = ''
  if post.anonymous:
    nameblock += '<span class="postername">'

    if post.email:
      nameblock += '<a href="mailto:' + post.email + '">' + ANONYMOUS + '</a>'
    else:
      nameblock += ANONYMOUS
          
    nameblock += '</span>'
  else:
    if post.author:
      nameblock += u'<span class="loggedin"><span class="loggedinblip">◆</span>'
    else:
      nameblock += '<span class="postername">'
    if post.email:
      nameblock += '<a href="mailto:' + post.email + '">'
    if post.author:
      nameblock += post.author.nickname()
    else:
      if post.name:
        nameblock += post.name
      else:
        if not post.tripcode:
          nameblock += ANONYMOUS
      if post.tripcode:
        nameblock += '</span><span class="postertrip">' + POST_TRIPCODE_CHARACTER + post.tripcode
    if post.email:
      nameblock += '</a>'
    if post.author:
      nameblock += u'<span class="loggedinblip">◆</span>'
    nameblock += '</span>'

  return nameblock

def checkNotFlooding(self, isreply):
  post = Post.all().filter('ip = ', self.request.remote_addr)
  if isreply:
    limit = SECONDS_BETWEEN_REPLIES
  else:
    limit = SECONDS_BETWEEN_NEW_THREADS
  post = post.order('-date').get()
  
  if post:
    timedifference = (datetime.now() - post.date)
    if timedifference.seconds < limit:
      return False

  return True

def checkNotBanned(self, address):
  ban = Ban.all().filter('ip = ', address).get()
  
  if ban:
    return False

  return True

def clickableURLs(message):
  translate_prog = prog = re.compile(r'\b(http|ftp|https)://\S+(\b|/)|\b[-.\w]+@[-.\w]+')
  i = 0
  list = []
  while 1:
    m = prog.search(message, i)
    if not m:
      break
    j = m.start()
    list.append(message[i:j])
    i = j
    url = m.group(0)
    while url[-1] in '();:,.?\'"<>':
      url = url[:-1]
    i = i + len(url)
    url = url
    if ':' in url:
      repl = '<a href="%s">%s</a>' % (url, url)
    else:
      repl = '<a href="mailto:%s">&lt;%s&gt;</a>' % (url, url)
    list.append(repl)
  j = len(message)
  list.append(message[i:j])
  return string.join(list, '')

def checkRefLinks(message, parentid):
  # TODO: Make this use a callback and allow reference links to posts in other threads
  message = re.compile(r'&gt;&gt;([0-9]+)').sub('<a href="/res/' + str(parentid) + r'.html#\1" onclick="javascript:highlight(' + '\'' + r'\1' + '\'' + r', true);">&gt;&gt;\1</a>', message)
  
  return message

def checkQuotes(message):
  message = re.compile(r'^&gt;(.*)$', re.MULTILINE).sub(r'<span class="unkfunc">&gt;\1</span>', message)
  
  return message

def checkAllowedHTML(message):
  message = re.compile(r'&lt;b&gt;(.*)&lt;/b&gt;', re.DOTALL | re.IGNORECASE).sub(r'<b>\1</b>', message)
  message = re.compile(r'&lt;i&gt;(.*)&lt;/i&gt;', re.DOTALL | re.IGNORECASE).sub(r'<i>\1</i>', message)
  message = re.compile(r'&lt;u&gt;(.*)&lt;/u&gt;', re.DOTALL | re.IGNORECASE).sub(r'<u>\1</u>', message)
  message = re.compile(r'&lt;strike&gt;(.*)&lt;/strike&gt;', re.DOTALL | re.IGNORECASE).sub(r'<strike>\1</strike>', message)
  # TODO: Fix double newlines
  message = re.compile(r'&lt;pre&gt;(.*)&lt;/pre&gt;', re.DOTALL | re.IGNORECASE).sub(r'<pre>\1</pre>', message)
  
  return message

def checkImageNotDuplicate(image):
  image_hex = sha224(image).hexdigest()
  post = Post.all().filter('image_hex = ', image_hex).get()
  if post:
    if post.parentid:
      return False, post.parent().key(), post.postid
    else:
      return False, post.key(), post.postid
  else:
    return True, 0, 0
  
def getThumbDimensions(width, height, maxsize):
  wratio = (float(maxsize) / float(width))
  hratio = (float(maxsize) / float(height))
  
  if (width <= maxsize) and (height <= maxsize):
    return width, height
  else:
    if (wratio * height) < maxsize:
      thumb_height = math.ceil(wratio * height)
      thumb_width = maxsize
    else:
      thumb_width = math.ceil(hratio * width)
      thumb_height = maxsize
  
  return int(thumb_width), int(thumb_height)

def getImage(key):
  image = Image.get(key)
  
  return image

def displayImage(self, key, mime, thumb=False, thumbcat=False):
  image = getImage(key)
  if image:
    self.response.headers['Expires'] = 'Thu, 15 Apr 2010 20:00:00 GMT'
    self.response.headers['Cache-Control'] = 'max-age=3600,public'

    if (thumbcat or thumb) and image.thumb_data:
      if str(mime) == 'image/png':
        self.response.headers['Content-Type'] = 'image/jpeg'
      else:
        self.response.headers['Content-Type'] = str(mime)

      if thumbcat and image.thumb_catalog_data:
        self.response.out.write(image.thumb_catalog_data)
      else:
        self.response.out.write(image.thumb_data)
    else:
      self.response.headers['Content-Type'] = str(mime)

      self.response.out.write(image.data)

def getImageInfo(data):
    data = str(data)
    size = len(data)
    height = -1
    width = -1
    content_type = ''

    # handle GIFs
    if (size >= 10) and data[:6] in ('GIF87a', 'GIF89a'):
        # Check to see if content_type is correct
        content_type = 'image/gif'
        w, h = struct.unpack("<HH", data[6:10])
        width = int(w)
        height = int(h)

    # See PNG 2. Edition spec (http://www.w3.org/TR/PNG/)
    # Bytes 0-7 are below, 4-byte chunk length, then 'IHDR'
    # and finally the 4-byte width, height
    elif ((size >= 24) and data.startswith('\211PNG\r\n\032\n')
          and (data[12:16] == 'IHDR')):
        content_type = 'image/png'
        w, h = struct.unpack(">LL", data[16:24])
        width = int(w)
        height = int(h)

    # Maybe this is for an older PNG version.
    elif (size >= 16) and data.startswith('\211PNG\r\n\032\n'):
        # Check to see if we have the right content type
        content_type = 'image/png'
        w, h = struct.unpack(">LL", data[8:16])
        width = int(w)
        height = int(h)

    # handle JPEGs
    elif (size >= 2) and data.startswith('\377\330'):
        content_type = 'image/jpeg'
        jpeg = StringIO(data)
        jpeg.read(2)
        b = jpeg.read(1)
        try:
            while (b and ord(b) != 0xDA):
                while (ord(b) != 0xFF): b = jpeg.read
                while (ord(b) == 0xFF): b = jpeg.read(1)
                if (ord(b) >= 0xC0 and ord(b) <= 0xC3):
                    jpeg.read(3)
                    h, w = struct.unpack(">HH", jpeg.read(4))
                    break
                else:
                    jpeg.read(int(struct.unpack(">H", jpeg.read(2))[0])-2)
                b = jpeg.read(1)
            width = int(w)
            height = int(h)
        except struct.error:
            pass
        except ValueError:
            pass

    return content_type, width, height

# Counter code by vrypan.net
class Counter():
  """Unique counters for Google Datastore.
  Usage: c=Counter('hits').inc() will increase the counter 'hits' by 1 and return the new value.
  When your application is run for the first time, you should call the create(start_value) method."""
  def __init__(self, name):
    self.__name = name
    res = db.GqlQuery("SELECT * FROM Idx WHERE name = :1 LIMIT 1", self.__name).fetch(1)
    if (len(res)==0):
      self.__status = 0
    else:
      self.__status = 1
      self.__key = res[0].key()
  
  def create(self, start_value=0):
    """This method is NOT "thread safe". Even though some testing is done,
    the developer is responsible make sure it is only called once for each counter.
    This should not be a problem, since it sould only be used during application installation.
    """

    res = db.GqlQuery("SELECT * FROM Idx WHERE name = :1 LIMIT 1", self.__name).fetch(1)
    if (len(res)==0):
      C = Idx(name=self.__name, count=start_value)
      self.__key = C.put()
      self.__status = 1
    else:
      raise ValueError, 'Counter: '+ self.__name +' already exists'
  
  def get(self):
    self.__check_sanity__()
    return db.get(self.__key).count
  
  def inc(self):
    self.__check_sanity__()
    db.run_in_transaction(self.__inc1__)
    return self.get()
  
  def __check_sanity__(self):
    if (self.__status==0):
      s = Counter('Post_ID').create()
      #raise ValueError, 'Counter: '+self.__name+' does not exist in Idx'
    else:
      pass
  
  def __inc1__(self):
    obj = db.get(self.__key)
    obj.count += 1
    obj.put()

def real_main():
  global _DEBUG, time_start
  time_start = datetime.now()
  
  application = webapp.WSGIApplication([('/', MainPage),
                                        (r'/res/(.*).html/(.*)', ResPage),
                                        (r'/res/(.*).html', ResPage),
                                        (r'/cat/(.*)/(.*)', ViewImageThumbCat),
                                        (r'/thumb/(.*)/(.*)', ViewImageThumb),
                                        (r'/src/(.*)/(.*)', ViewImage),
                                        (r'/finish_c', FinishImageCat),
                                        (r'/finish', FinishImage),
                                        (r'/([0-9]+).html', MainPage),
                                        ('/post', Board),
                                        ('/delete', Delete),
                                        ('/catalog.html', Catalog),
                                        ('/panel', Panel),
                                        ('/search', Search),
                                        ('/sitemap.xml', Sitemap),
                                        (r'/admin/(.*)/(.*)', AdminPage),
                                        (r'/admin/(.*)', AdminPage),
                                        ('/admin', AdminPage)],
                                        debug=_DEBUG)
  run_wsgi_app(application)

def profile_main():
  import cProfile, pstats
  
  prof = cProfile.Profile()
  prof = prof.runctx("real_main()", globals(), locals())
  print "<pre>"
  stats = pstats.Stats(prof)
  stats.sort_stats("time")  # Or cumulative
  stats.print_stats(500)
  # stats.print_callees()
  # stats.print_callers()
  print "</pre>"

if __name__ == "__main__":
  #profile_main()
  real_main()
