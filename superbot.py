#!/usr/bin/env python
import sys
import glob
import os
import time
import logging
import argparse
import json
from importlib import import_module

from slackclient import SlackClient

sys.dont_write_bytecode = True

this_dir = os.path.dirname(os.path.realpath(__file__))

'''
Documentation for this file will be provided later.
I'm a bit too busy right now, sorry!
I suggest not trying to develop for SuperBot just
yet, until I can explain how.
'''


class SuperBot(object):
	def __init__(self, credentials, config={}):
		# set the config object
		self.config = config

		# set slack token
		self.tokens = credentials
		self.token = self.tokens.get('slack')

		# set working directory for loading plugins or other files
		self.directory = self.config.get('base_path', this_dir)
		
		if self.directory.startswith('~'):
			path = os.path.join(os.path.expanduser('~'), self.directory)
			self.directory = os.path.expanduser(path)
		elif not self.directory.startswith('/'):
			path = os.path.join(os.getcwd(), self.directory)
			self.directory = os.path.abspath(path)

		# establish logging
		log_file = self.config.get('logfile', 'superbot.log')
		logging.basicConfig(filename=log_file,
							level=logging.INFO,
							format='%(asctime)s %(message)s')
		logging.info('Initialized in: {}'.format(self.directory))
		self.debug = self.config.get('debug', True)
		self.verbose = self.config.get('verbose', True)

		self.username = self.config.get('username', 'superbot')
		self.usercode = self.config.get('usercode', '<@U249VP6H2>')

		# initialize stateful fields
		self.last_ping = 0
		self.plugin_names = ['anon_chat']
		self.plugin_instances = []
		self.slack_client = None

	def _dbg(self, debug_string):
		if self.debug:
			logging.info(debug_string)

	def connect(self):
		"""Convenience method that creates Server instance"""
		self.slack_client = SlackClient(self.token)
		self.slack_client.rtm_connect()

	def _start(self):
		self.connect()
		#self.find_plugins()
		self.load_plugins()
		while True:
			for reply in self.slack_client.rtm_read():
				self.event_handlers(reply)
			self.autoping()
			if os.path.isfile('superbot.stop'):
				sys.exit(0)
			time.sleep(.1)

	def start(self):
		if 'daemon' in self.config and self.config.get('daemon'):
			import daemonize
			pid_file = self.get_pid_file()
			daemon = daemonize.Daemonize(app='Slack-SuperBot', pid=pid_file, action=self._start)
			daemon.start()
		else:
			self._start()
	
	def get_pid_file(self):
		i=0
		while os.path.isfile('/tmp/superbot'+str(i)+'.pid'):
			i+=1
		return '/tmp/superbot'+str(i)+'.pid'

	def autoping(self):
		# hardcode the interval to 3 seconds
		now = int(time.time())
		if now > self.last_ping + 3:
			self.slack_client.server.ping()
			self.last_ping = now

	def log(self, text=None, ansi_code=None, force=False):
		if self.verbose or force:
			if text:
				if ansi_code:
					print('\033['+str(ansi_code)+'m' + text + '\033[0m')
				else:
					print(text)
			else:
				print()

	def message_addressed(self, data):
		if data["type"] == "message" and 'text' in data:
			text = data['text']
			
			if text.startswith(self.usercode):
				return True, 13
			elif text.startswith(self.username):
				return True, 9
			elif data['channel'] in (im['id'] for im in self.api_call('im.list')['ims']):
				return True, 0
		
		return False, 0

	def event_handlers(self, data):
		if "type" in data:
			self._dbg("got {}".format(data["type"]))

			self.handle_event(data)
			for plugin in self.plugin_instances:
				if self.debug:
					plugin.handle_event(data)
				else:
					try:
						plugin.handle_event(data)
					except Exception:
						logging.exception("problem in module {} {}".format(plugin, data))

	def handle_event(self, data):
		if data['type'] == 'hello':
			self.log(type(self).__name__ + " connected to Slack", 42)
			self.log()

		addressed, start = self.message_addressed(data)
		if addressed and data['text'][start:] in ('reload-plugins', 'plugin-reload', 'update', 'restart'):
			self.log('Reloading plugins', 33)
			message = "Reloading SuperBot plugins"
			self.send_message(data['channel'], message)
			self.load_plugins()

	def send_message(self, channel, message=None):
		channel = self.slack_client.server.channels.find(channel)
		if channel is not None and message is not None:
			channel.send_message(message)
			return True
		return False

	def get_username(self, user_id):
		for member in self.api_call('users.list')['members']:
			if member['id'] == user_id.upper():
				return member['name']

	def get_channel(self, channel_id):
		for channel in self.api_call('channels.list')['channels']:
			if channel['id'] == channel_id.upper():
				return channel['name']

	def api_call(self, method, kwargs={}):
		if method is not None:
			response = self.slack_client.server.api_call(method, **kwargs)
			return json.loads(response)

	def load_plugins(self):
		self.plugin_instances = []
		for name in self.plugin_names:
			module = import_module('plugins.'+name)
			if 'Plugin' in dir(module):
				instance = module.Plugin(self)
				self.plugin_instances.append(instance)

	def find_plugins(self):
		sys.path.insert(0, self.directory + '/markov/')
		for plugin in glob.glob(self.directory + '/markov/*'):
			sys.path.insert(1, plugin)

		for plugin in glob.glob(self.directory + '/markov/*.py'):
			logging.info(plugin)
			name = plugin.split('/')[-1][:-3]
			self.plugin_names.append(name)


class Plugin(object):
	def __init__(self, superbot):
		self.sb = superbot

	def handleEvent(self, data):
		raise NotImplementedError

def get_config():
	# Improve this later
	config = {}

	parser = argparse.ArgumentParser()
	#parser.add_argument('--debug', help='Break on plugin errors', action='store_true')
	parser.add_argument('--daemon', help='Run as a daemon', action='store_true')
	parser.add_argument('--credentials', help='Specify the credentials file', type=str)
	parser.add_argument('--config', help='Specify a config file', type=str)
	parsed = parser.parse_args()

	config['daemon'] = parsed.daemon
	config['credentials'] = parsed.credentials
	
	try:
		config.update(json.load(open(parsed.config or 'config.json')))
	except (IOError, ValueError):
		pass
	
	return config
	

def main():
	config = get_config()
	credentials = json.load(open(config['credentials'] or 'credentials.json'))
	bot = SuperBot(credentials, config)
	try:
		bot.start()
	except KeyboardInterrupt:
		sys.exit(0)

if __name__ == '__main__':
	main()
