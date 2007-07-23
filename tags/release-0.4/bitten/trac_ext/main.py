# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

import os

from trac.core import *
from trac.env import IEnvironmentSetupParticipant
from trac.perm import IPermissionRequestor
from trac.wiki import IWikiSyntaxProvider
from bitten.model import schema, schema_version, Build, BuildConfig


class BuildSystem(Component):

    implements(IEnvironmentSetupParticipant, IPermissionRequestor,
               IWikiSyntaxProvider)

    # IEnvironmentSetupParticipant methods

    def environment_created(self):
        # Create the required tables
        db = self.env.get_db_cnx()
        cursor = db.cursor()
        for table in schema:
            for stmt in db.to_sql(table):
                cursor.execute(stmt)

        # Insert a global version flag
        cursor.execute("INSERT INTO system (name,value) "
                       "VALUES ('bitten_version',%s)", (schema_version,))

        # Create the directory for storing snapshot archives
        snapshots_dir = os.path.join(self.env.path, 'snapshots')
        os.mkdir(snapshots_dir)

        db.commit()

    def environment_needs_upgrade(self, db):
        cursor = db.cursor()
        cursor.execute("SELECT value FROM system WHERE name='bitten_version'")
        row = cursor.fetchone()
        if not row or int(row[0]) < schema_version:
            return True

    def upgrade_environment(self, db):
        cursor = db.cursor()
        cursor.execute("SELECT value FROM system WHERE name='bitten_version'")
        row = cursor.fetchone()
        if not row:
            self.environment_created()
        else:
            current_version = int(row[0])
            from bitten import upgrades
            for version in range(current_version + 1, schema_version + 1):
                for function in upgrades.map.get(version):
                    function(self.env, db)
            cursor.execute("UPDATE system SET value=%s WHERE "
                           "name='bitten_version'", (schema_version,))
            self.log.info('Upgraded Bitten tables from version %d to %d',
                          current_version, schema_version)

    # IPermissionRequestor methods

    def get_permission_actions(self):
        actions = ['BUILD_VIEW', 'BUILD_CREATE', 'BUILD_MODIFY', 'BUILD_DELETE']
        return actions + [('BUILD_ADMIN', actions)]

    # IWikiSyntaxProvider methods

    def get_wiki_syntax(self):
        return []

    def get_link_resolvers(self):
        def _format_link(formatter, ns, name, label):
            build = Build.fetch(self.env, int(name))
            if build:
                config = BuildConfig.fetch(self.env, build.config)
                title = 'Build %d ([%s] of %s) by %s' % (build.id, build.rev,
                        config.label, build.slave)
                return '<a class="build" href="%s" title="%s">%s</a>' \
                       % (formatter.href.build(build.config, build.id), title,
                          label)
            return label
        yield 'build', _format_link