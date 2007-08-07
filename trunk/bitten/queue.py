# -*- coding: utf-8 -*-
#
# Copyright (C) 2007 Edgewall Software
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

Furthermore, the C{BuildQueue} class is used by the build master to determine
the next pending build, and to match build slaves against configured target
platforms.
"""

from itertools import ifilter
import logging
import re

from trac.versioncontrol import NoSuchNode
from bitten.model import BuildConfig, TargetPlatform, Build, BuildStep

log = logging.getLogger('bitten.queue')


def collect_changes(repos, config, db=None):
    """Collect all changes for a build configuration that either have already
    been built, or still need to be built.
    
    This function is a generator that yields C{(platform, rev, build)} tuples,
    where C{platform} is a L{bitten.model.TargetPlatform} object, C{rev} is the
    identifier of the changeset, and C{build} is a L{bitten.model.Build} object
    or C{None}.

    @param repos: the version control repository
    @param config: the build configuration
    @param db: a database connection (optional)
    """
    env = config.env
    if not db:
        db = env.get_db_cnx()
    try:
        node = repos.get_node(config.path)
    except NoSuchNode, e:
        env.log.warn('Node for configuration %r not found', config.name,
                     exc_info=True)
        return

    for path, rev, chg in node.get_history():

        # Don't follow moves/copies
        if path != repos.normalize_path(config.path):
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
        for platform in TargetPlatform.select(env, config.name, db=db):
            builds = list(Build.select(env, config.name, rev, platform.id,
                                       db=db))
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

    def __init__(self, env, build_all=False):
        """Create the build queue.
        
        @param env: the Trac environment
        @param build_all: whether older revisions should be built
        """
        self.env = env
        self.log = env.log
        self.build_all = build_all

    # Build scheduling

    def get_build_for_slave(self, name, properties):
        """Check whether one of the pending builds can be built by the build
        slave.
        
        If such a build is found, this method returns a C{(build, slave)}
        tuple, where C{build} is the L{bitten.model.Build} object and C{slave}
        is the name of the build slave that should handle the build.

        Otherwise, this function will return C{(None, None)}
        """
        log.debug('Checking for pending builds...')

        db = self.env.get_db_cnx()
        repos = self.env.get_repository()

        self.reset_orphaned_builds()

        # Iterate through pending builds by descending revision timestamp, to
        # avoid the first configuration/platform getting all the builds
        platforms = [p.id for p in self.match_slave(name, properties)]
        build = None
        builds_to_delete = []
        for build in Build.select(self.env, status=Build.PENDING, db=db):
            if self.should_delete_build(build, repos):
               builds_to_delete.append(build)
            elif build.platform in platforms:
                break
        else:
            self.log.debug('No pending builds.')
            return None

        # delete any obsolete builds
        for build in builds_to_delete:
            build.delete(db=db)

        if build:
            build.slave = name
            build.slave_info.update(properties)
            build.status = Build.IN_PROGRESS
            build.update(db=db)

        if build or builds_to_delete:
            db.commit()

        return build

    def match_slave(self, name, properties):
        """Match a build slave against available target platforms.
        
        @param name: The name of the slave
        @param properties: A dict containing the properties of the slave
        @return: the list of platforms the slave matched
        """
        platforms = []

        for config in BuildConfig.select(self.env):
            for platform in TargetPlatform.select(self.env, config=config.name):
                match = True
                for propname, pattern in ifilter(None, platform.rules):
                    try:
                        propvalue = properties.get(propname)
                        if not propvalue or not re.match(pattern, propvalue):
                            match = False
                            break
                    except re.error:
                        self.log.error('Invalid platform matching pattern "%s"',
                                       pattern, exc_info=True)
                        match = False
                        break
                if match:
                    self.log.debug('Slave %s matched target platform %r of '
                                   'build configuration %r', name,
                                   platform.name, config.name)
                    platforms.append(platform)

        return platforms

    def populate(self):
        """Add a build for the next change on each build configuration to the
        queue.

        The next change is the latest repository check-in for which there isn't
        a corresponding build on each target platform. Repeatedly calling this
        method will eventually result in the entire change history of the build
        configuration being in the build queue.
        """
        repos = self.env.get_repository()
        if hasattr(repos, 'sync'):
            repos.sync()

        db = self.env.get_db_cnx()
        builds = []

        for config in BuildConfig.select(self.env, db=db):
            for platform, rev, build in collect_changes(repos, config, db):
                if build is None:
                    self.log.info('Enqueuing build of configuration "%s" at '
                                  'revision [%s] on %s', config.name, rev,
                                  platform.name)
                    build = Build(self.env, config=config.name,
                                  platform=platform.id, rev=str(rev),
                                  rev_time=repos.get_changeset(rev).date)
                    builds.append(build)
                    break
                if not self.build_all:
                    self.log.debug('Ignoring older revisions for configuration '
                                   '%r', config.name)
                    break

        for build in builds:
            build.insert(db=db)

        db.commit()

    def reset_orphaned_builds(self):
        """Reset all in-progress builds to PENDING state.
        
        This is used to cleanup after a crash of the build master process,
        which would leave in-progress builds in the database that aren't
        actually being built because the slaves have disconnected.
        """
        db = self.env.get_db_cnx()
        for build in Build.select(self.env, status=Build.IN_PROGRESS, db=db):
            build.status = Build.PENDING
            build.slave = None
            build.slave_info = {}
            build.started = 0
            for step in list(BuildStep.select(self.env, build=build.id, db=db)):
                step.delete(db=db)
            build.update(db=db)
        db.commit()

    def should_delete_build(self, build, repos):
        # Ignore pending builds for deactived build configs
        config = BuildConfig.fetch(self.env, build.config)
        if not config.active:
            log.info('Dropping build of configuration "%s" at '
                     'revision [%s] on "%s" because the configuration is '
                     'deactivated', config.name, build.rev,
                     TargetPlatform.fetch(self.env, build.platform).name)
            return True

        # Stay within the revision limits of the build config
        if (config.min_rev and repos.rev_older_than(build.rev,
                                                    config.min_rev)) \
        or (config.max_rev and repos.rev_older_than(config.max_rev,
                                                    build.rev)):
            # This minimum and/or maximum revision has changed since
            # this build was enqueued, so drop it
            log.info('Dropping build of configuration "%s" at revision [%s] on '
                     '"%s" because it is outside of the revision range of the '
                     'configuration', config.name, build.rev,
                     TargetPlatform.fetch(self.env, build.platform).name)
            return True

        return False
