# coding: utf-8
# vim: set ts=4 sw=4 expandtab sts=4:
# Copyright (c) 2013-2014 Christian Geier & contributors
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
"""khal

Usage:
  khal calendar [-vc CONF] [ (-a CAL ... | -d CAL ... ) ] [DATE ...]
  khal agenda   [-vc CONF] [ (-a CAL ... | -d CAL ... ) ] [DATE ...]
  khal interactive [-vc CONF] [ (-a CAL ... | -d CAL ... ) ]
  khal new [-vc CONF] [-a cal] DESCRIPTION...
  khal printcalendars [-vc CONF]
  khal [-vc CONF] [ (-a CAL ... | -d CAL ... ) ] [DATE ...]
  khal (-h | --help)
  khal --version


Options:
  -h --help    Show this help.
  --version    Print version information.
  -a CAL       Use this calendars (can be used several times)
  -d CAL       Do not use this calendar (can be used several times)
  -v           Be extra verbose.
  -c CONF      Use this config file.

"""
import logging
import os
import re
import signal
import sys

try:
    from ConfigParser import RawConfigParser
    from ConfigParser import Error as ConfigParserError

except ImportError:
    from configparser import RawConfigParser
    from configparser import Error as ConfigParserError

try:
    from setproctitle import setproctitle
except ImportError:
    setproctitle = lambda x: None

from docopt import docopt
import pytz
import xdg

from khal import controllers
from khal import khalendar
from khal import __version__, __productname__
from khal.log import logger


def capture_user_interruption():
    """
    Tries to hide to the user the ugly python backtraces generated by
    pressing Ctrl-C.
    """
    signal.signal(signal.SIGINT, lambda x, y: sys.exit(0))


def _find_configuration_file():
    """Return the configuration filename.

    This function builds the list of paths known by khal and
    then return the first one which exists. The first paths
    searched are the ones described in the XDG Base Directory
    Standard. Each one of this path ends with
    DEFAULT_PATH/DEFAULT_FILE.

    On failure, the path DEFAULT_PATH/DEFAULT_FILE, prefixed with
    a dot, is searched in the home user directory. Ultimately,
    DEFAULT_FILE is searched in the current directory.
    """
    DEFAULT_FILE = __productname__ + '.conf'
    DEFAULT_PATH = __productname__
    resource = os.path.join(DEFAULT_PATH, DEFAULT_FILE)

    paths = []
    paths.extend([os.path.join(path, resource)
                  for path in xdg.BaseDirectory.xdg_config_dirs])
    paths.append(os.path.expanduser(os.path.join('~', '.' + resource)))
    paths.append(os.path.expanduser(DEFAULT_FILE))

    for path in paths:
        if os.path.exists(path):
            return path

    return None


class Namespace(dict):

    """The khal configuration holder.

    Mostly taken from pycarddav.

    This holder is a dict subclass that exposes its items as attributes.
    Inspired by NameSpace from argparse, Configuration is a simple
    object providing equality by attribute names and values, and a
    representation.

    Warning: Namespace instances do not have direct access to the dict
    methods. But since it is a dict object, it is possible to call
    these methods the following way: dict.get(ns, 'key')

    See http://code.activestate.com/recipes/577887-a-simple-namespace-class/
    """

    def __init__(self, obj=None):
        dict.__init__(self, obj if obj else {})

    def __dir__(self):
        return list(self)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, dict.__repr__(self))

    def __getattribute__(self, name):
        try:
            return self[name]
        except KeyError:
            msg = "'%s' object has no attribute '%s'"
            raise AttributeError(msg % (type(self).__name__, name))

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        del self[name]


class Section(object):

    def __init__(self, parser, group):
        self._parser = parser
        self._group = group
        self._schema = None
        self._parsed = {}

    def matches(self, name):
        return self._group == name.lower()

    def is_collection(self):
        return False

    def parse(self, section):
        failed = False
        if self._schema is None:
            return None

        for option, default, filter_ in self._schema:
            if filter_ is None:
                filter_ = lambda x: x
            try:
                self._parsed[option] = filter_(
                    self._parser.get(section, option)
                )
                self._parser.remove_option(section, option)
            except ConfigParserError:
                if default is None:
                    logger.error(
                        "Missing required option '{option}' in section "
                        "'{section}'".format(option=option, section=section))
                    failed = True
                self._parsed[option] = default
                # Remove option once handled (see the check function).
                self._parser.remove_option(section, option)

        if failed:
            return None
        else:
            return Namespace(self._parsed)

    @property
    def group(self):
        return self._group

    def _parse_bool_string(self, value):
        """if value is either 'True' or 'False' it returns that value as a
        bool, otherwise it returns the value"""
        value = value.strip().lower()
        if value in ['true', 'yes', '1']:
            return True
        else:
            return False

    def _parse_time_zone(self, value):
        """returns pytz timezone"""
        return pytz.timezone(value)

    def _parse_commands(self, command):
        commands = [
            'agenda', 'calendar', 'new', 'interactive', 'printcalendars']
        if command not in commands:
            logger.error("Invalid value '{}' for option 'default_command' in "
                         "section 'default'".format(command))
            return None
        else:
            return command


class CalendarSection(Section):

    def __init__(self, parser):
        Section.__init__(self, parser, 'calendars')
        self._schema = [
            ('path', None, lambda x: os.path.expanduser(os.path.expandvars(x))),
            ('readonly', False, self._parse_bool_string),
            ('color', '', None)
        ]

    def is_collection(self):
        return True

    def matches(self, name):
        match = re.match('calendar (?P<name>.*)', name, re.I)
        if match:
            self._parsed['name'] = match.group('name')
        return match is not None


class SQLiteSection(Section):

    def __init__(self, parser):
        Section.__init__(self, parser, 'sqlite')
        default_path = xdg.BaseDirectory.xdg_cache_home + '/khal/khal.db'
        self._schema = [
            ('path', default_path, lambda x: os.path.expanduser(os.path.expandvars(x))),
        ]


class LocaleSection(Section):
    def __init__(self, parser):
        Section.__init__(self, parser, 'locale')
        self._schema = [
            ('local_timezone', None, self._parse_time_zone),
            ('default_timezone', None, self._parse_time_zone),
            ('timeformat', None, None),
            ('dateformat', None, None),
            ('longdateformat', None, None),
            ('datetimeformat', None, None),
            ('longdatetimeformat', None, None),
            ('firstweekday', 0, int),
            ('encoding', 'utf-8', None),
            ('unicode_symbols', True, self._parse_bool_string),
        ]


class DefaultSection(Section):
    def __init__(self, parser):
        Section.__init__(self, parser, 'default')
        self._schema = [
            ('debug', False, self._parse_bool_string),
            ('default_command', 'calendar', self._parse_commands),
            ('default_calendar', True, None),
        ]


class ConfigParser(object):
    _sections = [
        DefaultSection, LocaleSection, SQLiteSection, CalendarSection
    ]

    _required_sections = [DefaultSection, LocaleSection, CalendarSection]

    def _get_section_parser(self, section):
        for cls in self._sections:
            parser = cls(self._conf_parser)
            if parser.matches(section):
                return parser
        return None

    def parse_config(self, cfile):
        self._conf_parser = RawConfigParser()
        try:
            if not self._conf_parser.read(cfile):
                logger.error("Cannot read config file' {}'".format(cfile))
                return None
        except ConfigParserError as error:
            logger.error("Could not parse config file "
                         "'{}': {}".format(cfile, error))
            return None
        items = dict()
        failed = False
        for section in self._conf_parser.sections():
            parser = self._get_section_parser(section)
            if parser is None:
                logger.warning(
                    "Found unknown section '{}' in config file".format(section)
                )
                continue

            values = parser.parse(section)
            if values is None:
                failed = True
                continue
            if parser.is_collection():
                if parser.group not in items:
                    items[parser.group] = []
                items[parser.group].append(values)
            else:
                items[parser.group] = values

        failed = self.check_required(items) or failed
        self.warn_leftovers()
        self.dump(items)

        if failed:
            return None
        else:
            return Namespace(items)

    def check_required(self, items):
        groupnames = [sec(None).group for sec in self._required_sections]
        failed = False
        for group in groupnames:
            if group not in items:
                logger.error(
                    "Missing required section '{}'".format(group))
                failed = True
        return failed

    def warn_leftovers(self):
        for section in self._conf_parser.sections():
            for option in self._conf_parser.options(section):
                logger.warn("Ignoring unknow option '{}' in section "
                            "'{}'".format(option, section))

    def dump(self, conf, intro='Using configuration:', tab=0):
        """Dump the loaded configuration using the logging framework.

        The values displayed here are the exact values which are seen by
        the program, and not the raw values as they are read in the
        configuration file.
        """
        # TODO while this is fully functional it could be prettier
        logger.debug('{0}{1}'.format('\t' * tab, intro))

        if isinstance(conf, (Namespace, dict)):
            for name, value in sorted(dict.copy(conf).items()):
                if isinstance(value, (Namespace, dict, list)):
                    self.dump(value, '[' + name + ']', tab=tab + 1)
                else:
                    logger.debug('{0}{1}: {2}'.format('\t' * (tab + 1), name,
                                                      value))
        elif isinstance(conf, list):
            for o in conf:
                self.dump(o, '\t' * tab + intro + ':', tab + 1)


def validate(conf, logger):
    """
    validate the config
    """
    rval = True
    cal_name = conf.default.default_calendar
    if cal_name is True:
        conf.default.default_calendar = conf.calendars[0].name

    else:
        if cal_name not in [cal.name for cal in conf.calendars]:
            logger.fatal('{} is not a valid calendar'.format(cal_name))
            rval = False
    if rval:
        return conf
    else:
        return None


def main_khal():
    capture_user_interruption()

    # setting the process title so it looks nicer in ps
    # shows up as 'khal' under linux and as 'python: khal (python2.7)'
    # under FreeBSD, which is still nicer than the default
    setproctitle('khal')

    arguments = docopt(__doc__, version=__productname__ + ' ' + __version__,
                       options_first=False)

    if arguments['-c'] is None:
        arguments['-c'] = _find_configuration_file()
    if arguments['-c'] is None:
        sys.exit('Cannot find any config file, exiting')
    if arguments['-v']:
        logger.setLevel(logging.DEBUG)

    conf = ConfigParser().parse_config(arguments['-c'])

    # TODO use some existing lib and move all the validation from ConfigParse
    # into validate as well
    conf = validate(conf, logger)

    if conf is None:
        sys.exit('Invalid config file, exiting.')

    collection = khalendar.CalendarCollection()
    for cal in conf.calendars:
        if (cal.name in arguments['-a'] and arguments['-d'] == list()) or \
           (cal.name not in arguments['-d'] and arguments['-a'] == list()):
            collection.append(khalendar.Calendar(
                name=cal.name,
                dbpath=conf.sqlite.path,
                path=cal.path,
                readonly=cal.readonly,
                color=cal.color,
                unicode_symbols=conf.locale.unicode_symbols,
                local_tz=conf.locale.local_timezone,
                default_tz=conf.locale.default_timezone
            ))
    collection._default_calendar_name = conf.default.default_calendar
    commands = ['agenda', 'calendar', 'new', 'interactive', 'printcalendars']

    if not any([arguments[com] for com in commands]):

        arguments = docopt(__doc__,
                           version=__productname__ + ' ' + __version__,
                           argv=[conf.default.default_command] + sys.argv[1:])

        # arguments[conf.default.default_command] = True  # TODO

    if arguments['calendar']:
        controllers.Calendar(collection,
                             date=arguments['DATE'],
                             firstweekday=conf.locale.firstweekday,
                             encoding=conf.locale.encoding,
                             dateformat=conf.locale.dateformat,
                             longdateformat=conf.locale.longdateformat,
                             )
    elif arguments['agenda']:
        controllers.Agenda(collection,
                           date=arguments['DATE'],
                           firstweekday=conf.locale.firstweekday,
                           encoding=conf.locale.encoding,
                           dateformat=conf.locale.dateformat,
                           longdateformat=conf.locale.longdateformat,
                           )
    elif arguments['new']:
        controllers.NewFromString(collection, conf, arguments['DESCRIPTION'])
    elif arguments['interactive']:
        controllers.Interactive(collection, conf)
    elif arguments['printcalendars']:
        print('\n'.join(collection.names))


def main_ikhal():
    sys.argv = [sys.argv[0], 'interactive'] + sys.argv[1:]
    main_khal()
