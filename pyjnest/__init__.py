# -*- mode: python; coding: utf-8 -*-

import json
import logging
import time

import requests

logger = logging.getLogger('pyenest')

class Connection(object):
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.headers = {'User-Agent': 'Nest/1.1.0.10 CFNetwork/548.0.4',
                        'Accept-Language': 'en-us'}

        # all the users ever seen on this connection
        self._users = {}

        # all the devices ever seen on this connection
        self._devices = {}

        # all the structures ever seen on this connection
        self._structures = {}

    def login(self):
        logger.debug('logging in {}'.format(self.username))
        data = {'username': self.username,
                'password': self.password}

        r = requests.post('https://home.nest.com/user/login',
                          data = data,
                          headers = self.headers)
        res = r.json()

        self.transport_url = res['urls']['transport_url']
        self.access_token = res['access_token']
        self.userid = res['userid']

        self.headers['Authorization'] = 'Basic ' + self.access_token
        self.headers['X-nl-user-id'] = self.userid
        self.headers['X-nl-protocol-version'] = '1'

        self.update_status()

    def update_status(self):
        r = requests.get(self.transport_url + '/v2/mobile/user.{}'.format(self.userid),
                         headers = self.headers)

        self.status = r.json()

    @property
    def devices(self):
        return {device_id: Device.get(self, device_id) for device_id in self.status['device'].keys()}

    @property
    def links(self):
        return [(Device.get(self, device_id), Structure.get(self, data['structure'])) for device_id, data in self.status['link'].items()]

    @property
    def users(self):
        return {user_id: User.get(self, user_id) for user_id in self.status['user'].keys()}

    @property
    def structures(self):
        structure_ids = set([structure.structure_id for device, structure in self.links])
        return {structure_id: Structure.get(self, structure_id) for structure_id in structure_ids}

class User(object):
    @classmethod
    def get(klass, connection, user_id):
        if user_id in connection._users:
            return connection._users[user_id]
        return klass(connection, user_id)

    def __init__(self, connection, user_id):
        self.connection = connection
        self.user_id = user_id

        if self.user_id in self.connection._users:
            raise RuntimeError

        self.connection._users[self.user_id] = self

    def __getattr__(self, name):
        if name.startswith('_'):
            name = '$' + name[1:]

        if name in self.user:
            return self.user[name]
    
        raise AttributeError

    @property
    def user(self):
        return self.connection.status['user'][self.user_id]

    @property
    def settings(self):
        return UserSettings.get(self.connection, self.user_id)

    @property
    def structures(self):
        return {Structure.clean_id(structure_id): Structure.get(self.connection, structure_id) for structure_id in self.connection.status['user'][self.user_id]['structures']}

class UserSettings(object):
    @classmethod
    def get(klass, connection, user_id):
        if user_id in connection._user_settings:
            return connection._user_settings[user_id]
        return klass(connection, user_id)

    def __init__(self, connection, user_id):
        self.connection = connection
        self.user_id = user_id

        if self.user_id in self.connection._user_settings:
            raise RuntimeError

        self.connection._user_settings[self.user_id] = self

    def __getattr__(self, name):
        if name.startswith('_'):
            name = '$' + name[1:]

        if name in self.user_settings:
            return self.user_settings[name]
    
        raise AttributeError

    @property
    def user_settings(self):
        return self.connection.status['user_settings'][self.user_id]

    @property
    def user(self):
        return User.get(self.connection, self.user_id)

class Device(object):
    @classmethod
    def clean_id(klass, device_id):
        if device_id.startswith('device.'):
            return device_id[7:]

        return device_id

    @classmethod
    def get(klass, connection, device_id):
        device_id = klass.clean_id(device_id)

        if device_id in connection._devices:
            return connection._devices[device_id]

        return klass(connection, device_id)
        
    def __init__(self, connection, device_id):
        self.connection = connection
        self.device_id = self.clean_id(device_id)

        if self.device_id in self.connection._devices:
            raise RuntimeError

        if self.device_id not in self.connection.status['device']:
            raise RuntimeError

        if self.device_id not in self.connection.status['shared']:
            raise RuntimeError

        self.connection._devices[self.device_id] = self

    def __getattr__(self, name):
        if name.startswith('_'):
            name = '$' + name[1:]

        if name in self.device:
            return self.device[name]

        if name in self.shared:
            return self.shared[name]

        raise AttributeError

    @property
    def structure(self):
        return Structure.get(self.connection,
                             self.connection.status['link'][self.device_id]['structure'])

    @property
    def device(self):
        return self.connection.status['device'][self.device_id]

    @property
    def shared(self):
        return self.connection.status['shared'][self.device_id]

    @property
    def fan_mode(self):
        return self.device['fan_mode']

    @fan_mode.setter
    def fan_mode(self, value):
        if value not in ['auto', 'on']:
            raise ValueError('fan mode must be "auto" or "on"')

        data = {'fan_mode': value}

        headers = self.connection.headers.copy()
        headers['Content-Type'] = 'application/json'

        r = requests.post(self.connection.transport_url + '/v2/put/device.{}'.format(self.device_id),
                          data = json.dumps(data),
                          headers = headers)

        if r.status_code != 200:
            raise RuntimeError('Could not set the fan mode: "{}"'.format(r.text))

    def toggle_fan_mode(self):
        self.fan_mode = {'auto': 'on', 'on': 'auto'}[self.fan_mode]

    @property
    def target_temperature(self):
        return self.shared['target_temperature']

    @target_temperature.setter
    def target_temperature(self, value):
        data = {'target_change_pending': True,
                'target_temperature': value}

        headers = self.connection.headers.copy()
        headers['X-nl-base-version'] = '{}'.format(self.shared['$version'])
        headers['Content-Type'] = 'application/json'

        r = requests.post(self.connection.transport_url + '/v2/put/shared.{}'.format(self.device_id),
                          data = json.dumps(data),
                          headers = headers)

        if r.status_code != 200:
            raise RuntimeError('Could not set the target temperature: "{}"'.format(r.text))

class Structure(object):
    @classmethod
    def clean_id(klass, structure_id):
        if structure_id.startswith('structure.'):
            return structure_id[10:]

        return structure_id

    @classmethod
    def get(klass, connection, structure_id):
        structure_id = klass.clean_id(structure_id)

        if structure_id in connection._structures:
            return connection._structures[structure_id]

        return klass(connection, structure_id)

    def __init__(self, connection, structure_id):
        structure_id = self.clean_id(structure_id)

        self.connection = connection
        self.structure_id = structure_id

        if self.structure_id not in self.connection.status['structure']:
            raise RuntimeError

        if self.structure_id in self.connection._structures:
            raise RuntimeError
        
        self.connection._structures[self.structure_id] = self

    def __repr__(self):
        return 'Structure(\'{}\')'.format(self.structure_id)

    def __getattr__(self, name):
        if name.startswith('_'):
            name = '$' + name[1:]

        if name in self.structure:
            return self.structure[name]

        raise AttributeError

    @property
    def structure(self):
        return self.connection.status['structure'][self.structure_id]

    @property
    def devices(self):
        return {Device.clean_id(device_id): Device.get(self.connection, device_id) for device_id in self.structure['devices']}

    @property
    def away(self):
        return self.structure['away']

    @away.setter
    def away(self, value):
        if not isinstance(value, bool):
            raise ValueError('Away can only be set to True or False')

        data = {'away': value}
                'away_timestamp': int(time.time() * 1000),
                'away_setter': 0}

        headers = self.connection.headers.copy()
        headers['X-nl-base-version'] = '{}'.format(self.structure['$version'])
        headers['Content-Type'] = 'application/json'
        r = requests.post(self.connection.transport_url + '/v2/put/structure.{}'.format(self.structure_id),
                          data = data,
                          headers = headers)

        if r.status_code != 200:
            raise RuntimeError('Could not set away: "{}"'.format(r.text))

    def toggle_away(self):
        self.away = not self.away
