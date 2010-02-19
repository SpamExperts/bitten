# -*- coding: utf-8 -*-
#
# Copyright (C) 2007 Edgewall Software
# Copyright (C) 2005-2007 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

"""Automated upgrades for the Bitten database tables, and other data stored
in the Trac environment."""

import os
import sys

from trac.core import TracError
from trac.db import DatabaseManager
from trac.util.text import to_unicode
import codecs

__docformat__ = 'restructuredtext en'

# database abstraction functions

def _parse_scheme(env):
    """Retrieve the environment database scheme."""
    connection_uri = DatabaseManager(env).connection_uri
    parts = connection_uri.split(':', 1)
    scheme = parts[0].lower()
    return scheme

def update_sequence(env, db, tbl, col):
    """Update a sequence associated with an autoincrement column."""
    # Hopefully Trac will eventually implement its own version
    # of this function.
    scheme = _parse_scheme(env)
    if scheme == "postgres":
        seq = '%s_%s_seq' % (tbl, col)
        cursor = db.cursor()
        cursor.execute("SELECT setval('%s', (SELECT MAX(%s) FROM %s))"
            % (seq, col, tbl))

def drop_index(env, db, tbl, idx):
    """Drop an index associated with a table."""
    # Hopefully Trac will eventually implement its own version
    # of this function.
    scheme = _parse_scheme(env)
    cursor = db.cursor()
    if scheme == "mysql":
        cursor.execute("DROP INDEX %s ON %s" % (idx, tbl))
    else:
        cursor.execute("DROP INDEX %s" % (idx,))

# upgrade scripts

def add_log_table(env, db):
    """Add a table for storing the builds logs."""
    from trac.db import Table, Column
    INFO_LEVEL = 'I'

    cursor = db.cursor()

    build_log_schema_v3 = [
        Table('bitten_log', key='id')[
            Column('id', auto_increment=True), Column('build', type='int'),
            Column('step'), Column('type')
        ],
        Table('bitten_log_message', key=('log', 'line'))[
            Column('log', type='int'), Column('line', type='int'),
            Column('level', size=1), Column('message')
        ]
    ]

    build_step_schema_v3 = [
        Table('bitten_step', key=('build', 'name'))[
            Column('build', type='int'), Column('name'), Column('description'),
            Column('status', size=1), Column('started', type='int'),
            Column('stopped', type='int')
        ]
    ]

    connector, _ = DatabaseManager(env)._get_connector()
    for table in build_log_schema_v3:
        for stmt in connector.to_sql(table):
            cursor.execute(stmt)

    update_cursor = db.cursor()
    cursor.execute("SELECT build,name,log FROM bitten_step "
                   "WHERE log IS NOT NULL")
    for build, step, log in cursor:
        update_cursor.execute("INSERT INTO bitten_log (build, step) "
                "VALUES (%s,%s)", (build, step))
        log_id = db.get_last_id(update_cursor, 'bitten_log')
        messages = [(log_id, line, INFO_LEVEL, msg)
            for line, msg in enumerate(log.splitlines())]
        update_cursor.executemany("INSERT INTO bitten_log_message (log, line, level, message) "
            "VALUES (%s, %s, %s, %s)", messages)

    cursor.execute("CREATE TEMPORARY TABLE old_step AS SELECT * FROM bitten_step")
    cursor.execute("DROP TABLE bitten_step")
    for table in build_step_schema_v3:
        for stmt in connector.to_sql(table):
            cursor.execute(stmt)
    cursor.execute("INSERT INTO bitten_step (build,name,description,status,"
                   "started,stopped) SELECT build,name,description,status,"
                   "started,stopped FROM old_step")

def add_recipe_to_config(env, db):
    """Add a column for storing the build recipe to the build configuration
    table."""
    from bitten.model import BuildConfig
    cursor = db.cursor()

    cursor.execute("CREATE TEMPORARY TABLE old_config AS "
                   "SELECT * FROM bitten_config")
    cursor.execute("DROP TABLE bitten_config")

    connector, _ = DatabaseManager(env)._get_connector()
    for table in BuildConfig._schema:
        for stmt in connector.to_sql(table):
            cursor.execute(stmt)

    cursor.execute("INSERT INTO bitten_config (name,path,active,recipe,min_rev,"
                   "max_rev,label,description) SELECT name,path,0,'',NULL,"
                   "NULL,label,description FROM old_config")

def add_config_to_reports(env, db):
    """Add the name of the build configuration as metadata to report documents
    stored in the BDB XML database."""

    from bitten.model import Build
    try:
        from bsddb3 import db as bdb
        import dbxml
    except ImportError:
        return

    dbfile = os.path.join(env.path, 'db', 'bitten.dbxml')
    if not os.path.isfile(dbfile):
        return

    dbenv = bdb.DBEnv()
    dbenv.open(os.path.dirname(dbfile),
               bdb.DB_CREATE | bdb.DB_INIT_LOCK | bdb.DB_INIT_LOG |
               bdb.DB_INIT_MPOOL | bdb.DB_INIT_TXN, 0)

    mgr = dbxml.XmlManager(dbenv, 0)
    xtn = mgr.createTransaction()
    container = mgr.openContainer(dbfile, dbxml.DBXML_TRANSACTIONAL)
    uc = mgr.createUpdateContext()

    container.addIndex(xtn, '', 'config', 'node-metadata-equality-string', uc)

    qc = mgr.createQueryContext()
    for value in mgr.query(xtn, 'collection("%s")/report' % dbfile, qc):
        doc = value.asDocument()
        metaval = dbxml.XmlValue()
        if doc.getMetaData('', 'build', metaval):
            build_id = int(metaval.asNumber())
            build = Build.fetch(env, id=build_id, db=db)
            if build:
                doc.setMetaData('', 'config', dbxml.XmlValue(build.config))
                container.updateDocument(xtn, doc, uc)
            else:
                # an orphaned report, for whatever reason... just remove it
                container.deleteDocument(xtn, doc, uc)

    xtn.commit()
    container.close()
    dbenv.close(0)

def add_order_to_log(env, db):
    """Add order column to log table to make sure that build logs are displayed
    in the order they were generated."""
    from bitten.model import BuildLog
    cursor = db.cursor()

    cursor.execute("CREATE TEMPORARY TABLE old_log_v5 AS "
                   "SELECT * FROM bitten_log")
    cursor.execute("DROP TABLE bitten_log")

    connector, _ = DatabaseManager(env)._get_connector()
    for stmt in connector.to_sql(BuildLog._schema[0]):
        cursor.execute(stmt)

    cursor.execute("INSERT INTO bitten_log (id,build,step,generator,orderno) "
                   "SELECT id,build,step,type,0 FROM old_log_v5")

def add_report_tables(env, db):
    """Add database tables for report storage."""
    from bitten.model import Report
    cursor = db.cursor()

    connector, _ = DatabaseManager(env)._get_connector()
    for table in Report._schema:
        for stmt in connector.to_sql(table):
            cursor.execute(stmt)

def xmldb_to_db(env, db):
    """Migrate report data from Berkeley DB XML to SQL database.
    
    Depending on the number of reports stored, this might take rather long.
    After the upgrade is done, the bitten.dbxml file (and any BDB XML log files)
    may be deleted. BDB XML is no longer used by Bitten.
    """
    from bitten.model import Report
    from bitten.util import xmlio
    try:
        from bsddb3 import db as bdb
        import dbxml
    except ImportError:
        return

    dbfile = os.path.join(env.path, 'db', 'bitten.dbxml')
    if not os.path.isfile(dbfile):
        return

    dbenv = bdb.DBEnv()
    dbenv.open(os.path.dirname(dbfile),
               bdb.DB_CREATE | bdb.DB_INIT_LOCK | bdb.DB_INIT_LOG |
               bdb.DB_INIT_MPOOL | bdb.DB_INIT_TXN, 0)

    mgr = dbxml.XmlManager(dbenv, 0)
    xtn = mgr.createTransaction()
    container = mgr.openContainer(dbfile, dbxml.DBXML_TRANSACTIONAL)

    def get_pylint_items(xml):
        for problems_elem in xml.children('problems'):
            for problem_elem in problems_elem.children('problem'):
                item = {'type': 'problem'}
                item.update(problem_elem.attr)
                yield item

    def get_trace_items(xml):
        for cov_elem in xml.children('coverage'):
            item = {'type': 'coverage', 'name': cov_elem.attr['module'],
                    'file': cov_elem.attr['file'],
                    'percentage': cov_elem.attr['percentage']}
            lines = 0
            line_hits = []
            for line_elem in cov_elem.children('line'):
                lines += 1
                line_hits.append(line_elem.attr['hits'])
            item['lines'] = lines
            item['line_hits'] = ' '.join(line_hits)
            yield item

    def get_unittest_items(xml):
        for test_elem in xml.children('test'):
            item = {'type': 'test'}
            item.update(test_elem.attr)
            for child_elem in test_elem.children():
                item[child_elem.name] = child_elem.gettext()
            yield item

    qc = mgr.createQueryContext()
    for value in mgr.query(xtn, 'collection("%s")/report' % dbfile, qc, 0):
        doc = value.asDocument()
        metaval = dbxml.XmlValue()
        build, step = None, None
        if doc.getMetaData('', 'build', metaval):
            build = metaval.asNumber()
        if doc.getMetaData('', 'step', metaval):
            step = metaval.asString()

        report_types = {'pylint':   ('lint', get_pylint_items),
                        'trace':    ('coverage', get_trace_items),
                        'unittest': ('test', get_unittest_items)}
        xml = xmlio.parse(value.asString())
        report_type = xml.attr['type']
        category, get_items = report_types[report_type]
        sys.stderr.write('.')
        sys.stderr.flush()
        report = Report(env, build, step, category=category,
                        generator=report_type)
        report.items = list(get_items(xml))
        try:
            report.insert(db=db)
        except AssertionError:
            # Duplicate report, skip
            pass
    sys.stderr.write('\n')
    sys.stderr.flush()

    xtn.abort()
    container.close()
    dbenv.close(0)

def normalize_file_paths(env, db):
    """Normalize the file separator in file names in reports."""
    cursor = db.cursor()
    cursor.execute("SELECT report,item,value FROM bitten_report_item "
                   "WHERE name='file'")
    rows = cursor.fetchall() or []
    for report, item, value in rows:
        if '\\' in value:
            cursor.execute("UPDATE bitten_report_item SET value=%s "
                           "WHERE report=%s AND item=%s AND name='file'",
                           (value.replace('\\', '/'), report, item))

def fixup_generators(env, db):
    """Upgrade the identifiers for the recipe commands that generated log
    messages and report data."""

    mapping = {
        'pipe': 'http://bitten.edgewall.org/tools/sh#pipe',
        'make': 'http://bitten.edgewall.org/tools/c#make',
        'distutils': 'http://bitten.edgewall.org/tools/python#distutils',
        'exec_': 'http://bitten.edgewall.org/tools/python#exec' # Ambigious
    }
    cursor = db.cursor()
    cursor.execute("SELECT id,generator FROM bitten_log "
                   "WHERE generator IN (%s)"
                   % ','.join([repr(key) for key in mapping.keys()]))
    for log_id, generator in cursor:
        cursor.execute("UPDATE bitten_log SET generator=%s "
                       "WHERE id=%s", (mapping[generator], log_id))

    mapping = {
        'unittest': 'http://bitten.edgewall.org/tools/python#unittest',
        'trace': 'http://bitten.edgewall.org/tools/python#trace',
        'pylint': 'http://bitten.edgewall.org/tools/python#pylint'
    }
    cursor.execute("SELECT id,generator FROM bitten_report "
                   "WHERE generator IN (%s)"
                   % ','.join([repr(key) for key in mapping.keys()]))
    for report_id, generator in cursor:
        cursor.execute("UPDATE bitten_report SET generator=%s "
                       "WHERE id=%s", (mapping[generator], report_id))

def add_error_table(env, db):
    """Add the bitten_error table for recording step failure reasons."""
    from trac.db import Table, Column

    table = Table('bitten_error', key=('build', 'step', 'orderno'))[
                Column('build', type='int'), Column('step'), Column('message'),
                Column('orderno', type='int')
            ]
    cursor = db.cursor()

    connector, _ = DatabaseManager(env)._get_connector()
    for stmt in connector.to_sql(table):
        cursor.execute(stmt)

def add_filename_to_logs(env, db):
    """Add filename column to log table to save where log files are stored."""
    from bitten.model import BuildLog
    cursor = db.cursor()

    cursor.execute("CREATE TEMPORARY TABLE old_log_v8 AS "
                   "SELECT * FROM bitten_log")
    cursor.execute("DROP TABLE bitten_log")

    connector, _ = DatabaseManager(env)._get_connector()
    for stmt in connector.to_sql(BuildLog._schema[0]):
        cursor.execute(stmt)

    cursor.execute("INSERT INTO bitten_log (id,build,step,generator,orderno,filename) "
                   "SELECT id,build,step,generator,orderno,'' FROM old_log_v8")

def migrate_logs_to_files(env, db):
    """Migrates logs that are stored in the bitten_log_messages table into files."""
    from trac.db import Table, Column
    from bitten.model import BuildLog
    log_table = BuildLog._schema[0]
    logs_dir = env.config.get("bitten", "logs_dir", "log/bitten")
    if not os.path.isabs(logs_dir):
        logs_dir = os.path.join(env.path, logs_dir)
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    message_table = Table('bitten_log_message', key=('log', 'line'))[
            Column('log', type='int'), Column('line', type='int'),
            Column('level', size=1), Column('message')
        ]

    cursor = db.cursor()
    message_cursor = db.cursor()
    update_cursor = db.cursor()
    cursor.execute("SELECT id FROM bitten_log")
    for log_id, in cursor.fetchall():
        filename = "%s.log" % (log_id,)
        message_cursor.execute("SELECT message, level FROM bitten_log_message WHERE log=%s ORDER BY line", (log_id,))
        full_filename = os.path.join(logs_dir, filename)
        message_file = codecs.open(full_filename, "wb", "UTF-8")
        # Note: the original version of this code erroneously wrote to filename + ".level" instead of ".levels", producing unused level files
        level_file = codecs.open(full_filename + '.levels', "wb", "UTF-8")
        for message, level in message_cursor.fetchall() or []:
            message_file.write(to_unicode(message) + "\n")
            level_file.write(to_unicode(level) + "\n")
        message_file.close()
        level_file.close()
        update_cursor.execute("UPDATE bitten_log SET filename=%s WHERE id=%s", (filename, log_id))
        env.log.info("Migrated log %s", log_id)
    env.log.warning("Logs have been migrated from the database to files in %s. "
        "Ensure permissions are set correctly on this file. "
        "Since we presume that the migration worked correctly, "
        "we are now dropping the bitten_log_message table in the database (aren't you glad you backed up)", logs_dir)
    cursor.close()
    cursor = db.cursor()
    cursor.execute("DROP TABLE bitten_log_message")
    cursor.close()
    env.log.warning("We have dropped the bitten_log_message table - you may want to vaccuum/compress your database to save space")

def fix_log_levels_misnaming(env, db):
    """Renames or removes *.log.level files created by older versions of migrate_logs_to_files."""
    logs_dir = env.config.get("bitten", "logs_dir", "log/bitten")
    if not os.path.isabs(logs_dir):
        logs_dir = os.path.join(env.path, logs_dir)
    if not os.path.isdir(logs_dir):
        return

    rename_count = 0
    rename_error_count = 0
    delete_count = 0
    delete_error_count = 0

    for wrong_filename in os.listdir(logs_dir):
        if not wrong_filename.endswith('.log.level'):
            continue

        log_filename = os.path.splitext(wrong_filename)[0]
        right_filename = log_filename + '.levels'
        full_log_filename = os.path.join(logs_dir, log_filename)
        full_wrong_filename = os.path.join(logs_dir, wrong_filename)
        full_right_filename = os.path.join(logs_dir, right_filename)

        if not os.path.exists(full_log_filename):
            try:
                os.remove(full_wrong_filename)
                delete_count += 1
                env.log.info("Deleted stray log level file %s", wrong_filename)
            except Exception, e:
                delete_error_count += 1
                env.log.warning("Error removing stray log level file %s: %s", wrong_filename, e)
        else:
            if os.path.exists(full_right_filename):
                env.log.warning("Error renaming %s to %s in fix_log_levels_misnaming: new filename already exists",
                    full_wrong_filename, full_right_filename)
                rename_error_count += 1
                continue
            try:
                os.rename(full_wrong_filename, full_right_filename)
                rename_count += 1
                env.log.info("Renamed incorrectly named log level file %s to %s", wrong_filename, right_filename)
            except Exception, e:
                env.log.warning("Error renaming %s to %s in fix_log_levels_misnaming: %s", full_wrong_filename, full_right_filename, e)
                rename_error_count += 1

    env.log.info("Renamed %d incorrectly named log level files from previous migrate (%d errors)", rename_count, rename_error_count)
    env.log.info("Deleted %d stray log level (%d errors)", delete_count, delete_error_count)

def recreate_rule_with_int_id(env, db):
        """Recreates the bitten_rule table with an integer id column rather than a text one."""
        from bitten.model import TargetPlatform
        cursor = db.cursor()
        connector, _ = DatabaseManager(env)._get_connector()

        for table in TargetPlatform._schema:
            if table.name is 'bitten_rule':
                env.log.info("Migrating bitten_rule table to integer ids")
                cursor.execute("CREATE TEMPORARY TABLE old_rule AS SELECT * FROM bitten_rule")
                cursor.execute("DROP TABLE bitten_rule")
                for stmt in connector.to_sql(table):
                    cursor.execute(stmt)
                cursor.execute("INSERT INTO bitten_rule (id,propname,pattern,orderno) SELECT %s,propname,pattern,orderno FROM old_rule" % db.cast('id', 'int'))

def add_config_platform_rev_index_to_build(env, db):
    """Adds a unique index on (config, platform, rev) to the bitten_build table.
       Also drops the old index on bitten_build that serves no real purpose anymore."""
    # check for existing duplicates
    duplicates_cursor = db.cursor()
    build_cursor = db.cursor()

    duplicates_cursor.execute("SELECT config, rev, platform FROM bitten_build GROUP BY config, rev, platform HAVING COUNT(config) > 1")
    duplicates_exist = False
    for config, rev, platform in duplicates_cursor.fetchall():
        if not duplicates_exist:
            duplicates_exist = True
            print "\nConfig Name, Revision, Platform :: [<list of build ids>]"
            print "--------------------------------------------------------"

        build_cursor.execute("SELECT id FROM bitten_build WHERE config='%s' AND rev='%s' AND platform='%s'" % (config, rev, platform))
        build_ids = [row[0] for row in build_cursor.fetchall()]
        print "%s, %s, %s :: %s" % (config, rev, platform, build_ids)

    if duplicates_exist:
        print "--------------------------------------------------------\n"
        print "Duplicate builds found. You can remove the builds you don't want to"
        print "keep by using this one-line command:\n"
        print "$  python -c \"from bitten.model import Build; from trac.env import Environment; " \
                        "Build.fetch(Environment('%s'), <buildid>).delete()\"" % env.path
        print "\n...where <buildid> is the id of the build to remove.\n"
        print "Upgrades cannot be performed until conflicts are resolved."
        print "The upgrade script will now exit with an error:\n"

    duplicates_cursor.close()
    build_cursor.close()

    if not duplicates_exist:
        cursor = db.cursor()
        scheme = _parse_scheme(env)
        if scheme == "mysql":
            # 111 = 333 / len(columns in index) -- this is the Trac default
            cursor.execute("CREATE UNIQUE INDEX bitten_build_config_rev_platform_idx ON bitten_build (config(111), rev(111), platform)")
        else:
            cursor.execute("CREATE UNIQUE INDEX bitten_build_config_rev_platform_idx ON bitten_build (config,rev,platform)")
        drop_index(env, db, 'bitten_build', 'bitten_build_config_rev_slave_idx')
    else:
        raise TracError('')

def fix_sequences(env, db):
    """Fixes any auto increment sequences that might have been left in an inconsistent state.

       Upgrade scripts for schema versions > 10 should handle sequence updates correctly themselves.
       """
    update_sequence(env, db, 'bitten_build', 'id')
    update_sequence(env, db, 'bitten_log', 'id')
    update_sequence(env, db, 'bitten_platform', 'id')
    update_sequence(env, db, 'bitten_report', 'id')


map = {
    2: [add_log_table],
    3: [add_recipe_to_config],
    4: [add_config_to_reports],
    5: [add_order_to_log, add_report_tables, xmldb_to_db],
    6: [normalize_file_paths, fixup_generators],
    7: [add_error_table],
    8: [add_filename_to_logs,migrate_logs_to_files],
    9: [recreate_rule_with_int_id],
   10: [add_config_platform_rev_index_to_build, fix_sequences],
   11: [fix_log_levels_misnaming],
}
