# -*- coding: utf-8 -*-
#
# Copyright (C) 2007-2010 Edgewall Software
# Copyright (C) 2005-2007 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

"""Implements the scheduling of builds for a project.

This module provides the functionality for scheduling builds for a specific
Trac environment. It is used by both the build master and the web interface to
get the list of required builds (revisions not built yet).

Furthermore, the `BuildQueue` class is used by the build master to determine
the next pending build, and to match build slaves against configured target
platforms.
"""

from itertools import ifilter
import re
import time

from trac.util.datefmt import to_timestamp
from trac.util import pretty_timedelta, format_datetime
from trac.attachment import Attachment


from bitten.model import BuildConfig, TargetPlatform, Build, BuildStep
from bitten.util.repository import get_repos

__docformat__ = 'restructuredtext en'


def collect_changes(config, authname=None):
    """Collect all changes for a build configuration that either have already
    been built, or still need to be built.
    
    This function is a generator that yields ``(platform, rev, build)`` tuples,
    where ``platform`` is a `TargetPlatform` object, ``rev`` is the identifier
    of the changeset, and ``build`` is a `Build` object or `None`.

    :param config: the build configuration
    :param authname: the logged in user
    :param db: a database connection (optional)
    """
    env = config.env

    repos_name, repos, repos_path = get_repos(env, config.path, authname)

    with env.db_query as db:
        try:
            node = repos.get_node(repos_path)
        except Exception, e:
            env.log.warn('Error accessing path %r for configuration %r',
                        repos_path, config.name, exc_info=True)
            return

        for path, rev, chg in node.get_history():

            # Don't follow moves/copies
            if path != repos.normalize_path(repos_path):
                break

            # Stay within the limits of the build config
            if config.min_rev and repos.rev_older_than(rev, config.min_rev):
                break
            if config.max_rev and repos.rev_older_than(config.max_rev, rev):
                continue

            # Make sure the repository directory isn't empty at this
            # revision
            old_node = repos.get_node(path, rev)
            is_empty = True
            for entry in old_node.get_entries():
                is_empty = False
                break
            if is_empty:
                continue

        # For every target platform, check whether there's a build
        # of this revision
        for platform in TargetPlatform.select(env, config.name):
            builds = list(Build.select(env, config.name, rev, platform.id))
            if builds:
                build = builds[0]
            else:
                build = None

            yield platform, rev, build


class BuildQueue(object):
    """Enapsulates the build queue of an environment.
    
    A build queue manages the the registration of build slaves and detection of
    repository revisions that need to be built.
    """

    def __init__(self, env, build_all=False, stabilize_wait=0, timeout=0):
        """Create the build queue.
        
        :param env: the Trac environment
        :param build_all: whether older revisions should be built
        :param stabilize_wait: The time in seconds to wait before considering
                        the repository stable to create a build in the queue.
        :param timeout: the time in seconds after which an in-progress build
                        should be considered orphaned, and reset to pending
                        state
        """
        self.env = env
        self.log = env.log
        self.build_all = build_all
        self.stabilize_wait = stabilize_wait
        self.timeout = timeout

    # Build scheduling

    def get_build_for_slave(self, name, properties):
        """Check whether one of the pending builds can be built by the build
        slave.
        
        :param name: the name of the slave
        :type name: `basestring`
        :param properties: the slave configuration
        :type properties: `dict`
        :return: the allocated build, or `None` if no build was found
        :rtype: `Build`
        """
        self.log.debug('Checking for pending builds...')

        self.reset_orphaned_builds()

        # Iterate through pending builds by descending revision timestamp, to
        # avoid the first configuration/platform getting all the builds
        platforms = [p.id for p in self.match_slave(name, properties)]
        builds_to_delete = []
        build_found = False
        for build in Build.select(self.env, status=Build.PENDING):
            config_path = BuildConfig.fetch(self.env, name=build.config).path
            _name, repos, _path = get_repos(self.env, config_path, None)
            if self.should_delete_build(build, repos):
                self.log.info('Scheduling build %d for deletion', build.id)
                builds_to_delete.append(build)
            elif build.platform in platforms:
                build_found = True
                break
        if not build_found:
            self.log.debug('No pending builds.')
            build = None

        # delete any obsolete builds
        for build_to_delete in builds_to_delete:
            build_to_delete.delete()

        if build:
            build.slave = name
            build.slave_info.update(properties)
            build.status = Build.IN_PROGRESS
            build.update()

        return build

    def match_slave(self, name, properties):
        """Match a build slave against available target platforms.
        
        :param name: the name of the slave
        :type name: `basestring`
        :param properties: the slave configuration
        :type properties: `dict`
        :return: the list of platforms the slave matched
        """
        platforms = []

        for config in BuildConfig.select(self.env):
            for platform in TargetPlatform.select(self.env, config=config.name):
                match = True
                for propname, pattern in ifilter(None, platform.rules):
                    try:
                        propvalue = properties.get(propname)
                        if not propvalue or not re.match(pattern,
                                                         propvalue, re.I):
                            match = False
                            break
                    except re.error:
                        self.log.error('Invalid platform matching pattern "%s"',
                                       pattern, exc_info=True)
                        match = False
                        break
                if match:
                    self.log.debug('Slave %r matched target platform %r of '
                                   'build configuration %r', name,
                                   platform.name, config.name)
                    platforms.append(platform)

        if not platforms:
            self.log.warning('Slave %r matched none of the target platforms',
                             name)

        return platforms

    def populate(self):
        """Add a build for the next change on each build configuration to the
        queue.

        The next change is the latest repository check-in for which there isn't
        a corresponding build on each target platform. Repeatedly calling this
        method will eventually result in the entire change history of the build
        configuration being in the build queue.
        """
        builds = []

        for config in BuildConfig.select(self.env):
            platforms = []
            for platform, rev, build in collect_changes(config):

                if not self.build_all and platform.id in platforms:
                    # We've seen this platform already, so these are older
                    # builds that should only be built if built_all=True
                    self.log.debug('Ignoring older revisions for configuration '
                                   '%r on %r', config.name, platform.name)
                    break

                platforms.append(platform.id)

                if build is None:
                    self.log.info('Enqueuing build of configuration "%s" at '
                                  'revision [%s] on %s', config.name, rev,
                                  platform.name)
                    _repos_name, repos, _repos_path = get_repos(
                                    self.env, config.path, None)

                    rev_time = to_timestamp(repos.get_changeset(rev).date)
                    age = int(time.time()) - rev_time
                    if self.stabilize_wait and age < self.stabilize_wait:
                        self.log.info('Delaying build of revision %s until %s '
                                      'seconds pass. Current age is: %s '
                                      'seconds' % (rev, self.stabilize_wait,
                                      age))
                        continue

                    build = Build(self.env, config=config.name,
                                  platform=platform.id, rev=str(rev),
                                  rev_time=rev_time)
                    builds.append(build)

        for build in builds:
            try:
                build.insert()
            except Exception, e:
                # really only want to catch IntegrityErrors raised when
                # a second slave attempts to add builds with the same
                # (config, platform, rev) as an existing build.
                self.log.info('Failed to insert build of configuration "%s" '
                    'at revision [%s] on platform [%s]: %s',
                    build.config, build.rev, build.platform, e)
                raise

    def reset_orphaned_builds(self):
        """Reset all in-progress builds to ``PENDING`` state if they've been
        running so long that the configured timeout has been reached.
        
        This is used to cleanup after slaves that have unexpectedly cancelled
        a build without notifying the master, or are for some other reason not
        reporting back status updates.
        """
        if not self.timeout:
            # If no timeout is set, none of the in-progress builds can be
            # considered orphaned
            return

        with self.env.db_transaction as db:
            now = int(time.time())
            for build in Build.select(self.env, status=Build.IN_PROGRESS):
                if now - build.last_activity < self.timeout:
                    # This build has not reached the timeout yet, assume it's still
                    # being executed
                    continue

                self.log.info('Orphaning build %d. Last activity was %s (%s)' % \
                                  (build.id, format_datetime(build.last_activity),
                                   pretty_timedelta(build.last_activity)))

                build.status = Build.PENDING
                build.slave = None
                build.slave_info = {}
                build.started = 0
                build.stopped = 0
                build.last_activity = 0
                for step in list(BuildStep.select(self.env, build=build.id)):
                    step.delete()
                build.update()

                Attachment.delete_all(self.env, 'build', build.resource.id)
        #commit

    def should_delete_build(self, build, repos):
        config = BuildConfig.fetch(self.env, build.config)
        config_name = config and config.name \
                        or 'unknown config "%s"' % build.config

        platform = TargetPlatform.fetch(self.env, build.platform)
        # Platform may or may not exist anymore - get safe name for logging
        platform_name = platform and platform.name \
                        or 'unknown platform "%s"' % build.platform

        # Drop build if platform no longer exists
        if not platform:
            self.log.info('Dropping build of configuration "%s" at '
                     'revision [%s] on %s because the platform no longer '
                     'exists', config.name, build.rev, platform_name)
            return True

        # Ignore pending builds for deactived build configs
        if not (config and config.active):
            self.log.info('Dropping build of configuration "%s" at '
                     'revision [%s] on %s because the configuration is '
                     'deactivated', config_name, build.rev, platform_name)
            return True

        # Stay within the revision limits of the build config
        if (config.min_rev and repos.rev_older_than(build.rev,
                                                    config.min_rev)) \
        or (config.max_rev and repos.rev_older_than(config.max_rev,
                                                    build.rev)):
            self.log.info('Dropping build of configuration "%s" at revision [%s] on '
                     '"%s" because it is outside of the revision range of the '
                     'configuration', config.name, build.rev, platform_name)
            return True

        # If not 'build_all', drop if a more recent revision is available
        if not self.build_all and \
                len(list(Build.select(self.env, config=build.config,
                min_rev_time=build.rev_time, platform=build.platform))) > 1:
            self.log.info('Dropping build of configuration "%s" at revision [%s] '
                     'on "%s" because a more recent build exists',
                         config.name, build.rev, platform_name)
            return True

        return False
