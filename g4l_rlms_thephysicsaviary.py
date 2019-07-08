# -*-*- encoding: utf-8 -*-*-

import os
import re
import ast
import sys
import time
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue
import functools
import traceback
import pprint

import requests
import webpage2html
from bs4 import BeautifulSoup

from flask import Blueprint, request, url_for
from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler, LabNotFoundError, register_blueprint
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions
from labmanager.rlms.queue import QueueTask, run_tasks

    
def dbg(msg):
    if DEBUG:
        print "[%s]" % time.asctime(), msg
        sys.stdout.flush()

def dbg_lowlevel(msg, scope):
    if DEBUG_LOW_LEVEL:
        print "[%s][%s][%s]" % (time.asctime(), threading.current_thread().name, scope), msg
        sys.stdout.flush()


class PhysicsAviaryAddForm(AddForm):

    DEFAULT_URL = 'http://www.thephysicsaviary.com/'
    DEFAULT_LOCATION = 'United States'
    DEFAULT_PUBLICLY_AVAILABLE = True
    DEFAULT_PUBLIC_IDENTIFIER = 'physicsaviary'
    DEFAULT_AUTOLOAD = True

    def __init__(self, add_or_edit, *args, **kwargs):
        super(PhysicsAviaryAddForm, self).__init__(*args, **kwargs)
        self.add_or_edit = add_or_edit

    @staticmethod
    def process_configuration(old_configuration, new_configuration):
        return new_configuration


class PhysicsAviaryFormCreator(BaseFormCreator):

    def get_add_form(self):
        return PhysicsAviaryAddForm

MIN_TIME = datetime.timedelta(hours=24)

def get_languages():
    return ['en'] # 'it', 'es']

def get_laboratories():
    labs_and_identifiers  = PHYSICSAVIARY.cache.get('get_laboratories',  min_time = MIN_TIME)
    if labs_and_identifiers:
        labs, identifiers = labs_and_identifiers
        return labs, identifiers

    index = requests.get('https://www.thephysicsaviary.com/Physics/Programs/Labs/index.html').text
    soup = BeautifulSoup(index, 'lxml')


    identifiers = {
        # identifier: {
        #     'name': name,
        #     'link': link,
        #     'message': (message)
        # }
    }

    main_table = soup.find(id="MainTable")

    for table in main_table.find_all("table"):
        for lab_row in table.find_all("tr"):
            anchor_element = lab_row.find("a")
            if not anchor_element:
                continue

            fig_caption = anchor_element.find('figcaption')
            if not fig_caption:
                continue
            
            name = fig_caption.text
            href = 'https://www.thephysicsaviary.com/Physics/Programs/Labs/' + anchor_element['href']
            identifier = anchor_element['href']
            translations = {}

            lab_contents_text = requests.get(href).text
            lab_contents = BeautifulSoup(lab_contents_text, 'lxml')
            translations_js = lab_contents.find('script', src='translations.js')
            if translations_js:
                translations_js_data = requests.get(href.rsplit('/', 1)[0] + '/translations.js').text

                if 'TRANSLATION_DATA' in translations_js_data:
                    translations_data = ast.literal_eval(translations_js_data.strip().split('=', 1)[1].strip())
                    eng_translations = translations_data.get('messages', {}).get('en', {})
                    processed_translations = {

                    }
                    for key, value in eng_translations.items():
                        if isinstance(value, (str, unicode)):
                            processed_translations[key] = {
                                'value': value,
                            }
                    translations = {
                        'mails': {},
                        'translations': {
                            'en': processed_translations,
                        }
                    }

            identifiers[identifier] = {
                'name': name,
                'link': href,
                'translations': translations,
            }

    labs = []
    for identifier, identifier_data in identifiers.items():
        name = identifier_data['name']
        lab = Laboratory(name=name, laboratory_id=identifier, description=name)
        labs.append(lab)

    PHYSICSAVIARY.cache['get_laboratories'] = (labs, identifiers)
    return labs, identifiers

FORM_CREATOR = PhysicsAviaryFormCreator()

CAPABILITIES = [ Capabilities.WIDGET, Capabilities.URL_FINDER, Capabilities.CHECK_URLS, Capabilities.TRANSLATIONS, Capabilities.DOWNLOAD_LIST ]

class RLMS(BaseRLMS):

    DEFAULT_HEIGHT = '800'
    DEFAULT_SCALE = 8000

    def __init__(self, configuration, *args, **kwargs):
        self.configuration = json.loads(configuration or '{}')

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return CAPABILITIES

    def get_laboratories(self, **kwargs):
        labs, identifiers = get_laboratories()
        return labs

    def get_base_urls(self):
        return [ 'http://www.thephysicsaviary.com', 'https://www.thephysicsaviary.com', 'http://thephysicsaviary.com', 'https://thephysicsaviary.com' ]

    def get_translations(self, laboratory_id):
        labs, identifiers = get_laboratories()
        for identifier, identifier_data in identifiers.items():
            if identifier == laboratory_id:
                return identifier_data['translations']

        return { 'translations' : {}, 'mails' : {} }

    def get_lab_by_url(self, url):
        laboratories, identifiers = get_laboratories()

        url = url.split('?')[0]

        for lab in laboratories:
            if url.endswith(lab.laboratory_id):
                return lab

        return None

    def get_check_urls(self, laboratory_id):
        laboratories, identifiers = get_laboratories()
        lab_data = identifiers.get(laboratory_id)
        if lab_data:
            return [ lab_data['link'] ]
        return []

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        laboratories, identifiers = get_laboratories()
        if laboratory_id not in identifiers:
            raise LabNotFoundError("Laboratory not found: {}".format(laboratory_id))

        url = identifiers[laboratory_id]['link']

        lang = 'en'
        if 'locale' in kwargs:
            lang = kwargs['locale']
            if lang not in get_languages():
                lang = 'en'

        url = url.replace('LANG', lang)

        response = {
            'reservation_id' : url,
            'load_url' : url,
        }
        return response


    def load_widget(self, reservation_id, widget_name, **kwargs):
        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]

    def get_downloads(self, laboratory_id):
        return {
            'en_ALL': url_for('physicsaviary.physicsaviary_download', laboratory_id=laboratory_id, _external=True),
        }

def populate_cache(rlms):
    rlms.get_laboratories()

PHYSICSAVIARY = register("PhysicsAviary", ['1.0'], __name__)
PHYSICSAVIARY.add_local_periodic_task('Populating cache', populate_cache, hours = 15)

physicsaviary_blueprint = Blueprint('physicsaviary', __name__)

@physicsaviary_blueprint.route('/id/<path:laboratory_id>')
def physicsaviary_download(laboratory_id):
    laboratories, identifiers = get_laboratories()
    lab_data = identifiers.get(laboratory_id)
    if not lab_data:
        return "Not found", 404

    link = lab_data['link']
    generated = webpage2html.generate(index=link, keep_script=True, verbose=False)
    return generated.encode()


register_blueprint(physicsaviary_blueprint, url='/thephysicsaviary')

DEBUG = PHYSICSAVIARY.is_debug() or (os.environ.get('G4L_DEBUG') or '').lower() == 'true' or False
DEBUG_LOW_LEVEL = DEBUG and (os.environ.get('G4L_DEBUG_LOW') or '').lower() == 'true'

if DEBUG:
    print("Debug activated")

if DEBUG_LOW_LEVEL:
    print("Debug low level activated")

sys.stdout.flush()

if __name__ == '__main__':
    rlms = RLMS('{}')
    labs = rlms.get_laboratories()
    for lab in labs:
        print rlms.reserve(lab.laboratory_id, 'nobody', 'nowhere', '{}', [], {}, {})
