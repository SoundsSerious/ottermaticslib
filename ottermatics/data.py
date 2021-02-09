from sqlalchemy import *
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import relationship
from sqlalchemy.orm import backref
from sqlalchemy.orm import scoped_session
from sqlalchemy_utils import *

import functools
from twisted.internet import defer, reactor, threads, task
from twisted.python import context

import os
import logging
import numpy

import signal

from urllib.request import urlopen
from psycopg2.extensions import register_adapter, AsIs
from os import sys

from ottermatics.patterns import Singleton, SingletonMeta, singleton_meta_object
from ottermatics.logging import LoggingMixin, set_all_loggers_to, is_ec2_instance

from contextlib import contextmanager

import diskcache

log = logging.getLogger('otterlib-data')

def addapt_numpy_float64(numpy_float64):
    return AsIs(numpy_float64)

def addapt_numpy_int64(numpy_int64):
    return AsIs(numpy_int64)

def addapt_numpy_float32(numpy_float32):
    return AsIs(numpy_float32)

def addapt_numpy_int32(numpy_int32):
    return AsIs(numpy_int32)

def addapt_numpy_array(numpy_array):
    return AsIs(tuple(numpy_array))

register_adapter(numpy.float64, addapt_numpy_float64)
register_adapter(numpy.int64, addapt_numpy_int64)
register_adapter(numpy.float32, addapt_numpy_float32)
register_adapter(numpy.int32, addapt_numpy_int32)
register_adapter(numpy.ndarray, addapt_numpy_array)

#Backwards Compatability
PORT = 5432
HOST = 'localhost'
USER = 'postgres'
PASS = 'dumbpass'
DB_NAME = 'dumbdb'

DataBase = declarative_base()

#@Singleton

#@singleton_meta_object
class DiskCacheStore(LoggingMixin, metaclass=SingletonMeta):
    '''A singleton object with safe methods for file access,
    Aims to prevent large number of file pointers open
    
    These should be subclassed for each cache location you want'''

    _cache = None
    size_limit = 10E9 #10GB
    alt_path = None
    cache_class = diskcache.Cache
    timeout = 1.0
    cache_init_kwargs = None
    #cache_class = diskcache.FanoutCache
    # def __init__(self,**kwargs):
    #     '''Takes a path and size limit to create for the cache on startup,
    #     first initalize it cannot be chagned
    #     :param alt_path: a different name than the formatted name of the class
    #     :param size_limit: the size of that the cache should respect in bytes'''

    #     self.info('initalizing disk cache')
    #     if 'size_limit' in kwargs:
    #         self.size_limit = kwargs['size_limit']

    #     if 'alt_path' in kwargs:
    #         self.alt_path = kwargs['alt_path']            

    def __init__(self,**kwargs):
        self.cache_init_kwargs = kwargs

    @property
    def cache_root(self):
        if self.alt_path is not None:
            return os.path.join( 'cache' , self.alt_path)
        return os.path.join( 'cache' , '{}'.format(type(self).__name__).lower())
    
    @property
    def cache(self):
        if self._cache is None:
            self.debug('setting cache')
            self._cache = self.cache_class(self.cache_root,timeout=self.timeout,\
                                            size_limit=self.size_limit,** self.cache_init_kwargs)
        return self._cache


    def __getstate__(self):
        d = self.__dict__.copy()
        d['_cache'] = None #don't pickle file objects!
        return d

    def set(self,key,data,retry=True,**kwargs):
        '''Passes default arguments to set the key:data relationship
        :param expire: time in seconds to expire the data
        '''
        with self.cache as ch:
            ch.set(key,data,retry=retry,**kwargs)

    def get(self,key,on_missing=None,retry=True):
        '''Helper method to get an item, return None it doesn't exist and warn.
        :param on_missing: a callback to use if the data is missing, which will set the data at the key, and return it'''
        with self.cache as ch:
            if key in ch:
                return self.cache.get(key,retry=retry)
            else:
                if on_missing is not None:
                    data = on_missing()
                    self.set(key,data)
                    return data
                self.warning('key {} not in cache'.format(key))
        return None

    def expire(self):
        '''wrapper for diskcache expire method'''
        self.cache.expire()

    @property
    def current_keys(self):
        self.cache.expire()
        return set(list(self.cache))

    def __iter__(self):
        return self.cache.__iter__()

    @property
    def identity(self):
        return '{}:{}'.format(self.__class__.__name__.lower(), self.cache_root)


class DBConnection(LoggingMixin, metaclass=SingletonMeta):
    '''A database singleton that is thread safe and pickleable (serializable)
    to get the active instance use DBConnection.instance(**non_default_connection_args)'''

    _connection_template =  "postgresql://{user}:{passd}@{host}:{port}/{database}"
    pool_size=20
    max_overflow=0
    echo=False

    dbname = None
    host = None
    user = None
    passd = None
    port = 5432
    
    #Reset
    connection_string = None
    engine = None
    scopefunc = None
    session_factory = None
    Session = None

    def __init__(self,database_name=None,host=None,user=None,passd=None,**kwargs):
        '''On the Singleton DBconnection.instance(): __init__(*args,**kwargs) will get called, technically you 
        could do it this way but won't be thread safe, or a single instance
        :param database_name: the name for the database inside the db server
        :param host: hostname
        :param user: username
        :param passd: password
        :param port: hostname
        :param echo: if the engine echos or not'''
        self.info('initalizing db connection')
        #Get ENV Defaults
        self.load_configuration_from_env()

        if database_name is not None:
            self.dbname = database_name
        if host is not None:
            self.info("Getting DB host arg")
            self.host = host
        if user is not None:
            self.info("Getting DB user arg")
            self.user = user            
        if passd is not None:
            self.info("Getting DB pass arg")
            self.passd = passd

        if 'echo' in kwargs:
            self.info("Setting Echo")
            self.echo = kwargs['echo']
        else:
            self.echo = False    
        
        #Args with defaults
        if 'port' in kwargs:
            self.info("Getting DB port arg")
            self.port = kwargs['port'] 

        self.resetLog()
        self.configure()
    


    def configure(self):
        '''A boilerplate configure method'''
        self.info('Configuring...')
        self.connection_string = self._connection_template.format(host=self.host,user=self.user,passd=self.passd,
                                                                    port=self.port,database=self.dbname)
                                                                    
        self.engine = create_engine(self.connection_string,pool_size=self.pool_size, max_overflow=self.max_overflow)
        self.engine.echo = self.echo

        self.scopefunc = functools.partial(context.get, "uuid")

        self.session_factory  = sessionmaker(bind=self.engine,expire_on_commit=True)
        self.Session = scoped_session(self.session_factory, scopefunc = self.scopefunc)                

    @contextmanager
    def session_scope(self):
        """Provide a transactional scope around a series of operations."""
        if not hasattr(self,'Session'):
            self.configure()
        self.session = self.Session()
        try:
            yield self.session
            self.session.commit()
        except:
            self.session.rollback()
            raise
        finally:
            self.session.close()
        del self.session


    def load_configuration_from_env(self):
        global PORT, PASS, USER, HOST, DB_NAME #Backwards Compatability

        if 'DB_NAME' in os.environ:
            self.dbname = DB_NAME = os.environ['DB_NAME']
            self.info("Getting ENV DB_NAME")
            
        if 'DB_CONNECTION' in os.environ:
            self.host = HOST = os.environ['DB_CONNECTION']
            self.info("Getting ENV DB_CONNECTION")
            
        if 'DB_USER' in os.environ:
            self.user = USER = os.environ['DB_USER']
            self.info("Getting ENV DB_USER")
            
        if 'DB_PASS' in os.environ:
            self.passd = PASS = os.environ['DB_PASS']
            self.info("Getting ENV DB_PASS")
            
        if 'DB_PORT' in os.environ:
            self.port = PORT = os.environ['DB_PORT']
            self.info("Getting ENV DB_PORT")

        return PORT, PASS, USER, HOST


    def rebuild_database(self, confirm=True):
        '''Rebuild database on confirmation, create the database if nessicary'''
        if not is_ec2_instance():
            answer = input("We Are Going To Overwrite The Databse {}\nType 'CONFIRM' to continue:\n".format(HOST))
        else:
            answer = 'CONFIRM'

        if answer == 'CONFIRM' or confirm==False:
            #Create Database If It Doesn't Exist
            if not database_exists(self.connection_string):
                self.info("Creating Database")
                create_database(self.connection_string)
            else:
                #Otherwise Just Drop The Tables
                self.debug("Dropping DB Metadata")
                DataBase.metadata.drop_all(self.engine)
            #(Re)Create Tables
            self.debug("Creating DB Metadata")
            DataBase.metadata.create_all(self.engine)
        else:
            try:
                raise Exception("Ah ah ah you didn't say the magic word")
            except Exception as e:
                self.error(e)

    def ensure_database_exists(self):
        '''Check if database exists, if not create it and tables'''
        if not database_exists(self.connection_string):
            self.info("Creating Database")
            create_database(self.connection_string)
            DataBase.metadata.create_all(self.engine) 
            

    def cleanup_sessions(self):
        self.info("Closing All Active Sessions")
        self.Session.close_all()

    @property
    def identity(self):
        return 'DB Con: {s.user}@{s.dbname}'.format(s=self)

    def __getstate__(self):
        '''Remove active connection objects, they are not picklable'''
        d = self.__dict__.copy()
        d['connection_string'] = None
        d['engine'] = None
        d['scopefunc'] = None
        d['session_factory'] = None
        d['Session'] = None
        return d
    
    def __setstate__(self,d):
        '''We reconfigure on opening a pickle'''
        self.__dict__ = d
        self.configure()

