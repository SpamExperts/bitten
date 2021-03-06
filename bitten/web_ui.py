# -*- coding: utf-8 -*-
#
# Copyright (C) 2005-2007 Christopher Lenz <cmlenz@gmx.de>
# Copyright (C) 2007-2010 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

"""Implementation of the Bitten web interface."""

import posixpath
import re
import time
from StringIO import StringIO
from datetime import datetime

import pkg_resources
from genshi.builder import tag
from trac.attachment import AttachmentModule, Attachment
from trac.core import *
from trac.config import Option
from trac.mimeview.api import Context
from trac.perm import PermissionError
from trac.resource import Resource, get_resource_url
from trac.timeline import ITimelineEventProvider
from trac.util import escape, pretty_timedelta, format_datetime, shorten_line, \
                      Markup, arity
from trac.util.datefmt import to_timestamp, to_datetime, utc
from trac.util.html import html
from trac.web import IRequestHandler, IRequestFilter, HTTPNotFound
from trac.web.chrome import INavigationContributor, ITemplateProvider, \
                            add_link, add_stylesheet, add_ctxtnav, \
                            prevnext_nav, add_script, add_warning
from trac.versioncontrol import NoSuchChangeset, NoSuchNode
from trac.wiki import wiki_to_html, wiki_to_oneliner
from bitten.api import ILogFormatter, IReportChartGenerator, IReportSummarizer
from bitten.master import BuildMaster
from bitten.model import BuildConfig, TargetPlatform, Build, BuildStep, \
                         BuildLog, Report
from bitten.queue import collect_changes
from bitten.util.repository import get_repos, get_chgset_resource, display_rev
from bitten.util import json

_status_label = {Build.PENDING: 'pending',
                 Build.IN_PROGRESS: 'in progress',
                 Build.SUCCESS: 'completed',
                 Build.FAILURE: 'failed'}
_status_title = {Build.PENDING: 'Pending',
                 Build.IN_PROGRESS: 'In Progress',
                 Build.SUCCESS: 'Success',
                 Build.FAILURE: 'Failure'}
_step_status_label = {BuildStep.SUCCESS: 'success',
                      BuildStep.FAILURE: 'failed',
                      BuildStep.IN_PROGRESS: 'in progress'}

def _get_build_data(env, req, build, repos_name=None):
    chgset_url = ''
    if repos_name:
        chgset_resource = get_chgset_resource(env, repos_name, build.rev)
        chgset_url = get_resource_url(env, chgset_resource, req.href)
    platform = TargetPlatform.fetch(env, build.platform)
    data = {'id': build.id, 'name': build.slave, 'rev': build.rev,
            'status': _status_label[build.status],
            'platform': getattr(platform, 'name', 'unknown'),
            'cls': _status_label[build.status].replace(' ', '-'),
            'href': req.href.build(build.config, build.id),
            'chgset_href': chgset_url}
    if build.started:
        data['started'] = format_datetime(build.started)
        data['started_delta'] = pretty_timedelta(build.started)
        data['duration'] = pretty_timedelta(build.started)
    if build.stopped:
        data['stopped'] = format_datetime(build.stopped)
        data['stopped_delta'] = pretty_timedelta(build.stopped)
        data['duration'] = pretty_timedelta(build.stopped, build.started)
    data['slave'] = {
        'name': build.slave,
        'ipnr': build.slave_info.get(Build.IP_ADDRESS),
        'os_name': build.slave_info.get(Build.OS_NAME),
        'os_family': build.slave_info.get(Build.OS_FAMILY),
        'os_version': build.slave_info.get(Build.OS_VERSION),
        'machine': build.slave_info.get(Build.MACHINE),
        'processor': build.slave_info.get(Build.PROCESSOR)
    }
    return data

def _has_permission(perm, repos, path, rev=None, raise_error=False):
    if hasattr(repos, 'authz'):
        if not repos.authz.has_permission(path):
            if not raise_error:
                return False
            repos.authz.assert_permission(path)
    else:
        node = repos.get_node(path, rev)
        if not node.can_view(perm):
            if not raise_error:
                return False
            raise PermissionError('BROWSER_VIEW', node.resource)
    return True

class BittenChrome(Component):
    """Provides the Bitten templates and static resources."""

    implements(INavigationContributor, ITemplateProvider)

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        pass

    def get_navigation_items(self, req):
        """Return the navigation item for access the build status overview from
        the Trac navigation bar."""
        if 'BUILD_VIEW' in req.perm:
            status = ''
            if BuildMaster(self.env).quick_status:
                for config in BuildConfig.select(self.env,
                                                 include_inactive=False):
                    prev_rev = None
                    for platform, rev, build in collect_changes(config, req.authname):
                        if rev != prev_rev:
                            if prev_rev is not None:
                               break
                            prev_rev = rev
                        if build:
                            build_data = _get_build_data(self.env, req, build)
                            if build_data['status'] == 'failed':
                                status='bittenfailed'
                                break
                            if build_data['status'] == 'in progress':
                                status='bitteninprogress'
                            elif not status:
                                if (build_data['status'] == 'completed'):
                                    status='bittencompleted'
                if not status:
                    status='bittenpending'
            yield ('mainnav', 'build',
                   tag.a('Build Status', href=req.href.build(), accesskey=5,
                         class_=status))

    # ITemplatesProvider methods

    def get_htdocs_dirs(self):
        """Return the directories containing static resources."""
        return [('bitten', pkg_resources.resource_filename(__name__, 'htdocs'))]

    def get_templates_dirs(self):
        """Return the directories containing templates."""
        return [pkg_resources.resource_filename(__name__, 'templates')]


class BuildConfigController(Component):
    """Implements the web interface for build configurations."""

    implements(IRequestHandler, IRequestFilter, INavigationContributor)

    # Configuration options

    chart_style = Option('bitten', 'chart_style', 'height: 220px; width: 220px;', doc=
        """Style attribute for charts. Mostly useful for setting the height and width.""")

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'build'

    def get_navigation_items(self, req):
        return []

    # IRequestHandler methods

    def match_request(self, req):
        match = re.match(r'/build(?:/([\w.-]+))?/?$', req.path_info)
        if match:
            if match.group(1):
                req.args['config'] = match.group(1)
            return True

    def process_request(self, req):
        req.perm.require('BUILD_VIEW')

        action = req.args.get('action')
        view = req.args.get('view')
        config = req.args.get('config')

        if config:
            data = self._render_config(req, config)
        elif view == 'inprogress':
            data = self._render_inprogress(req)
        else:
            data = self._render_overview(req)

        add_stylesheet(req, 'bitten/bitten.css')
        return 'bitten_config.html', data, None

    # IRequestHandler methods

    def pre_process_request(self, req, handler):
        return handler

    def post_process_request(self, req, template, data, content_type):
        if template:
            add_stylesheet(req, 'bitten/bitten.css')

        return template, data, content_type

    # Internal methods

    def _render_overview(self, req):
        data = {'title': 'Build Status'}
        show_all = False
        if req.args.get('show') == 'all':
            show_all = True
        data['show_all'] = show_all

        configs = []
        for config in BuildConfig.select(self.env, include_inactive=show_all):
            repos_name, repos, repos_path = get_repos(self.env, config.path,
                                                      req.authname)
            rev = config.max_rev or repos.youngest_rev
            try:
                if not _has_permission(req.perm, repos, repos_path, rev=rev):
                    continue
            except NoSuchNode:
                add_warning(req, "Configuration '%s' points to non-existing "
                        "path '/%s' at revision '%s'. Configuration skipped." \
                                    % (config.name, config.path, rev))
                continue

            description = config.description
            if description:
                description = wiki_to_html(description, self.env, req)

            platforms_data = []
            for platform in TargetPlatform.select(self.env, config=config.name):
                pd = { 'name': platform.name,
                       'id': platform.id,
                       'builds_pending': len(list(Build.select(self.env,
                                config=config.name, status=Build.PENDING,
                                platform=platform.id))),
                       'builds_inprogress': len(list(Build.select(self.env,
                                config=config.name, status=Build.IN_PROGRESS,
                                platform=platform.id)))
                }
                platforms_data.append(pd)

            config_data = {
                'name': config.name, 'label': config.label or config.name,
                'active': config.active, 'path': config.path,
                'description': description,
                'builds_pending' : len(list(Build.select(self.env,
                                                config=config.name,
                                                status=Build.PENDING))),
                'builds_inprogress' : len(list(Build.select(self.env,
                                                config=config.name,
                                                status=Build.IN_PROGRESS))),
                'href': req.href.build(config.name),
                'builds': [],
                'platforms': platforms_data
            }
            configs.append(config_data)
            if not config.active:
                continue

            prev_rev = None
            for platform, rev, build in collect_changes(config, req.authname):
                if rev != prev_rev:
                    if prev_rev is None:
                        chgset = repos.get_changeset(rev)
                        chgset_resource = get_chgset_resource(self.env, 
                                repos_name, rev)
                        config_data['youngest_rev'] = {
                            'id': rev,
                            'href': get_resource_url(self.env, chgset_resource,
                                                     req.href),
                            'display_rev': display_rev(repos, rev),
                            'author': chgset.author or 'anonymous',
                            'date': format_datetime(chgset.date),
                            'message': wiki_to_oneliner(
                                shorten_line(chgset.message), self.env, req=req)
                        }
                    else:
                        break
                    prev_rev = rev
                if build:
                    build_data = _get_build_data(self.env, req, build, repos_name)
                    build_data['platform'] = platform.name
                    config_data['builds'].append(build_data)
                else:
                    config_data['builds'].append({
                        'platform': platform.name, 'status': 'pending'
                    })

        data['configs'] = sorted(configs, key=lambda x:x['label'].lower())
        data['page_mode'] = 'overview'

        in_progress_builds = Build.select(self.env, status=Build.IN_PROGRESS)
        pending_builds = Build.select(self.env, status=Build.PENDING)

        data['builds_pending'] = len(list(pending_builds))
        data['builds_inprogress'] = len(list(in_progress_builds))

        add_link(req, 'views', req.href.build(view='inprogress'),
                 'In Progress Builds')
        add_ctxtnav(req, 'In Progress Builds',
                    req.href.build(view='inprogress'))
        return data

    def _render_inprogress(self, req):
        data = {'title': 'In Progress Builds',
                'page_mode': 'view-inprogress'}

        configs = []
        for config in BuildConfig.select(self.env, include_inactive=False):
            repos_name, repos, repos_path = get_repos(self.env, config.path,
                                                      req.authname)
            rev = config.max_rev or repos.youngest_rev
            try:
                if not _has_permission(req.perm, repos, repos_path, rev=rev):
                    continue
            except NoSuchNode:
                add_warning(req, "Configuration '%s' points to non-existing "
                        "path '/%s' at revision '%s'. Configuration skipped." \
                                    % (config.name, config.path, rev))
                continue

            self.log.debug(config.name)
            if not config.active:
                continue

            in_progress_builds = Build.select(self.env, config=config.name,
                                              status=Build.IN_PROGRESS)

            current_builds = 0
            builds = []
            # sort correctly by revision.
            for build in sorted(in_progress_builds,
                                cmp=lambda x, y: int(y.rev_time) - int(x.rev_time)):
                rev = build.rev
                build_data = _get_build_data(self.env, req, build, repos_name)
                build_data['rev'] = rev
                build_data['rev_href'] = build_data['chgset_href']
                platform = TargetPlatform.fetch(self.env, build.platform)
                build_data['platform'] = platform.name
                build_data['steps'] = []

                for step in BuildStep.select(self.env, build=build.id):
                    build_data['steps'].append({
                        'name': step.name,
                        'description': step.description,
                        'duration': to_datetime(step.stopped or int(time.time()), utc) - \
                                    to_datetime(step.started, utc),
                        'status': _step_status_label[step.status],
                        'cls': _step_status_label[step.status].replace(' ', '-'),
                        'errors': step.errors,
                        'href': build_data['href'] + '#step_' + step.name
                    })

                builds.append(build_data)
                current_builds += 1

            if current_builds == 0:
                continue

            description = config.description
            if description:
                description = wiki_to_html(description, self.env, req)
            configs.append({
                'name': config.name, 'label': config.label or config.name,
                'active': config.active, 'path': config.path,
                'description': description,
                'href': req.href.build(config.name),
                'builds': builds
            })

        data['configs'] = sorted(configs, key=lambda x:x['label'].lower())
        return data

    def _render_config(self, req, config_name):

        config = BuildConfig.fetch(self.env, config_name)
        if not config:
            raise HTTPNotFound("Build configuration '%s' does not exist." \
                                % config_name)

        repos_name, repos, repos_path = get_repos(self.env, config.path,
                                                  req.authname)

        rev = config.max_rev or repos.youngest_rev
        try:
            _has_permission(req.perm, repos, repos_path, rev=rev,
                                                        raise_error=True)
        except NoSuchNode:
            raise TracError("Permission checking against repository path %s "
                "at revision %s failed." % (config.path, rev))

        data = {'title': 'Build Configuration "%s"' \
                          % config.label or config.name,
                'page_mode': 'view_config'}
        add_link(req, 'up', req.href.build(), 'Build Status')
        description = config.description
        if description:
            description = wiki_to_html(description, self.env, req)

        pending_builds = list(Build.select(self.env,
                                config=config.name, status=Build.PENDING))
        inprogress_builds = list(Build.select(self.env,
                                config=config.name, status=Build.IN_PROGRESS))

        min_chgset_url = ''
        if config.min_rev:
            min_chgset_resource = get_chgset_resource(self.env, repos_name,
                                                      config.min_rev)
            min_chgset_url = get_resource_url(self.env, min_chgset_resource,
                                              req.href),
        max_chgset_url = ''
        if config.max_rev:
            max_chgset_resource = get_chgset_resource(self.env, repos_name,
                                                      config.max_rev)
            max_chgset_url = get_resource_url(self.env, max_chgset_resource,
                                              req.href),

        data['config'] = {
            'name': config.name, 'label': config.label, 'path': config.path,
            'min_rev': config.min_rev,
            'min_rev_href': min_chgset_url,
            'max_rev': config.max_rev,
            'max_rev_href': max_chgset_url,
            'active': config.active, 'description': description,
            'browser_href': req.href.browser(config.path),
            'builds_pending' : len(pending_builds),
            'builds_inprogress' : len(inprogress_builds)
        }

        context = Context.from_request(req, config.resource)
        data['context'] = context
        data['config']['attachments'] = AttachmentModule(self.env).attachment_data(context)

        platforms = list(TargetPlatform.select(self.env, config=config_name))
        data['config']['platforms'] = [
            { 'name': platform.name,
              'id': platform.id,
              'builds_pending': len(list(Build.select(self.env,
                                                    config=config.name,
                                                    status=Build.PENDING,
                                                    platform=platform.id))),
              'builds_inprogress': len(list(Build.select(self.env,
                                                    config=config.name,
                                                    status=Build.IN_PROGRESS,
                                                    platform=platform.id)))
              }
            for platform in platforms
        ]

        has_reports = False
        for report in Report.select(self.env, config=config.name):
            has_reports = True
            break

        if has_reports:
            chart_generators = []
            report_categories = list(self._report_categories_for_config(config))
            for generator in ReportChartController(self.env).generators:
                for category in generator.get_supported_categories():
                    if category in report_categories:
                        chart_generators.append({
                            'href': req.href.build(config.name, 'chart/' + category),
                            'category': category,
                            'style': self.config.get('bitten', 'chart_style'),
                        })
            data['config']['charts'] = chart_generators

        page = max(1, int(req.args.get('page', 1)))
        more = False
        data['page_number'] = page

        builds_per_page = 12 * len(platforms)
        idx = 0
        builds = {}
        revisions = []
        build_order = []
        for platform, rev, build in collect_changes(config,authname=req.authname):
            if idx >= page * builds_per_page:
                more = True
                break
            elif idx >= (page - 1) * builds_per_page:
                if rev not in builds:
                    revisions.append(rev)
                builds.setdefault(rev, {})
                chgset_resource = get_chgset_resource(self.env, repos_name, rev)
                builds[rev].setdefault('href', get_resource_url(self.env,
                                                    chgset_resource, req.href))
                build_order.append((rev, repos.get_changeset(rev).date))
                builds[rev].setdefault('display_rev', display_rev(repos, rev))
                if build and build.status != Build.PENDING:
                    build_data = _get_build_data(self.env, req, build)
                    build_data['steps'] = []
                    for step in BuildStep.select(self.env, build=build.id):
                        build_data['steps'].append({
                            'name': step.name,
                            'description': step.description,
                            'duration': to_datetime(step.stopped or int(time.time()), utc) - \
                                        to_datetime(step.started, utc),
                            'status': _step_status_label[step.status],
                            'cls': _step_status_label[step.status].replace(' ', '-'),

                            'errors': step.errors,
                            'href': build_data['href'] + '#step_' + step.name
                        })
                    builds[rev][platform.id] = build_data
            idx += 1
        data['config']['build_order'] = [r[0] for r in sorted(build_order,
                                                            key=lambda x: x[1],
                                                            reverse=True)]
        data['config']['builds'] = builds
        data['config']['revisions'] = revisions

        if page > 1:
            if page == 2:
                prev_href = req.href.build(config.name)
            else:
                prev_href = req.href.build(config.name, page=page - 1)
            add_link(req, 'prev', prev_href, 'Previous Page')
        if more:
            next_href = req.href.build(config.name, page=page + 1)
            add_link(req, 'next', next_href, 'Next Page')
        if arity(prevnext_nav) == 4: # Trac 0.12 compat, see #450
            prevnext_nav(req, 'Previous Page', 'Next Page')
        else:
            prevnext_nav (req, 'Page')
        return data

    def _report_categories_for_config(self, config):
        """Yields the categories of reports that exist for active builds
        of this configuration.
        """


        for (category, ) in self.env.db_query("""SELECT DISTINCT report.category as category
FROM bitten_build AS build
JOIN bitten_report AS report ON (report.build=build.id)
WHERE build.config=%s AND build.rev_time >= %s AND build.rev_time <= %s""",
                       (config.name,
                        config.min_rev_time(self.env),
                        config.max_rev_time(self.env))):
            yield category


class BuildController(Component):
    """Renders the build page."""
    implements(INavigationContributor, IRequestHandler, ITimelineEventProvider)

    log_formatters = ExtensionPoint(ILogFormatter)
    report_summarizers = ExtensionPoint(IReportSummarizer)

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'build'

    def get_navigation_items(self, req):
        return []

    # IRequestHandler methods

    def match_request(self, req):
        match = re.match(r'/build/([\w.-]+)/(\d+)', req.path_info)
        if match:
            if match.group(1):
                req.args['config'] = match.group(1)
                if match.group(2):
                    req.args['id'] = match.group(2)
            return True

    def process_request(self, req):
        req.perm.require('BUILD_VIEW')

        build_id = int(req.args.get('id'))
        build = Build.fetch(self.env, build_id)
        if not build:
            raise HTTPNotFound("Build '%s' does not exist." \
                                % build_id)

        if req.method == 'POST':
            if req.args.get('action') == 'invalidate':
                self._do_invalidate(req, build)
            req.redirect(req.href.build(build.config, build.id))

        add_link(req, 'up', req.href.build(build.config),
                 'Build Configuration')
        data = {'title': 'Build %s - %s' % (build_id,
                                            _status_title[build.status]),
                'page_mode': 'view_build',
                'build': {}}
        config = BuildConfig.fetch(self.env, build.config)
        data['build']['config'] = {
            'name': config.label or config.name,
            'href': req.href.build(config.name)
        }

        context = Context.from_request(req, build.resource)
        data['context'] = context
        data['build']['attachments'] = AttachmentModule(self.env).attachment_data(context)

        formatters = []
        for formatter in self.log_formatters:
            formatters.append(formatter.get_formatter(req, build))

        summarizers = {} # keyed by report type
        for summarizer in self.report_summarizers:
            categories = summarizer.get_supported_categories()
            summarizers.update(dict([(cat, summarizer) for cat in categories]))

        repos_name, repos, repos_path = get_repos(self.env, config.path,
                                                  req.authname)

        _has_permission(req.perm, repos, repos_path, rev=build.rev, raise_error=True)

        data['build'].update(_get_build_data(self.env, req, build, repos_name))
        steps = []
        for step in BuildStep.select(self.env, build=build.id):
            steps.append({
                'name': step.name, 'description': step.description,
                'duration': pretty_timedelta(step.started, step.stopped or int(time.time())),
                'status': _step_status_label[step.status],
                'cls': _step_status_label[step.status].replace(' ', '-'),
                'errors': step.errors,
                'log': self._render_log(req, build, formatters, step),
                'reports': self._render_reports(req, config, build, summarizers,
                                                step)
            })
        data['build']['steps'] = steps
        data['build']['can_delete'] = ('BUILD_DELETE' in req.perm \
                                   and build.status != build.PENDING)

        chgset = repos.get_changeset(build.rev)
        data['build']['chgset_author'] = chgset.author
        data['build']['display_rev'] = display_rev(repos, build.rev)

        add_script(req, 'common/js/folding.js')
        add_script(req, 'bitten/tabset.js')
        add_script(req, 'bitten/jquery.flot.js')
        add_stylesheet(req, 'bitten/bitten.css')
        return 'bitten_build.html', data, None

    # ITimelineEventProvider methods

    def get_timeline_filters(self, req):
        if 'BUILD_VIEW' in req.perm:
            yield ('build', 'Builds')

    def get_timeline_events(self, req, start, stop, filters):
        if 'build' not in filters:
            return

        # Attachments (will be rendered by attachment module)
        for event in AttachmentModule(self.env).get_timeline_events(
            req, Resource('build'), start, stop):
            yield event

        start = to_timestamp(start)
        stop = to_timestamp(stop)

        add_stylesheet(req, 'bitten/bitten.css')

        with self.env.db_query as db:
            cursor = db.cursor()
            cursor.execute("SELECT b.id,b.config,c.label,c.path, b.rev,p.name,"
                           "b.stopped,b.status FROM bitten_build AS b"
                           "  INNER JOIN bitten_config AS c ON (c.name=b.config) "
                           "  INNER JOIN bitten_platform AS p ON (p.id=b.platform) "
                           "WHERE b.stopped>=%s AND b.stopped<=%s "
                           "AND b.status IN (%s, %s) ORDER BY b.stopped",
                           (start, stop, Build.SUCCESS, Build.FAILURE))

            event_kinds = {Build.SUCCESS: 'successbuild',
                           Build.FAILURE: 'failedbuild'}

            for id_, config, label, path, rev, platform, stopped, status in cursor:
                config_object = BuildConfig.fetch(self.env, config)
                repos_name, repos, repos_path = get_repos(self.env,
                                                          config_object.path,
                                                          req.authname)
                if not _has_permission(req.perm, repos, repos_path, rev=rev):
                    continue
                errors = []
                if status == Build.FAILURE:
                    for step in BuildStep.select(self.env, build=id_,
                                                 status=BuildStep.FAILURE):
                        errors += [(step.name, error) for error
                                   in step.errors]
                yield (event_kinds[status], to_datetime(stopped, utc), None,
                            (id_, config, label, display_rev(repos, rev), platform,
                                status, errors))

    def render_timeline_event(self, context, field, event):
        id_, config, label, rev, platform, status, errors = event[3]

        if field == 'url':
            return context.href.build(config, id_)

        elif field == 'title':
            return tag('Build of ', tag.em('%s [%s]' % (label, rev)),
                        ' on %s %s' % (platform, _status_label[status]))

        elif field == 'description':
            message = ''
            if context.req.args.get('format') == 'rss':
                if errors:
                    buf = StringIO()
                    prev_step = None
                    for step, error in errors:
                        if step != prev_step:
                            if prev_step is not None:
                                buf.write('</ul>')
                            buf.write('<p>Step %s failed:</p><ul>' \
                                      % escape(step))
                            prev_step = step
                        buf.write('<li>%s</li>' % escape(error))
                    buf.write('</ul>')
                    message = Markup(buf.getvalue())
            else:
                if errors:
                    steps = []
                    for step, error in errors:
                        if step not in steps:
                            steps.append(step)
                    steps = [Markup('<em>%s</em>') % step for step in steps]
                    if len(steps) < 2:
                        message = steps[0]
                    elif len(steps) == 2:
                        message = Markup(' and ').join(steps)
                    elif len(steps) > 2:
                        message = Markup(', ').join(steps[:-1]) + ', and ' + \
                                  steps[-1]
                    message = Markup('Step%s %s failed') % (
                                    len(steps) != 1 and 's' or '', message)
            return message

    # Internal methods

    def _do_invalidate(self, req, build):
        self.log.info('Invalidating build %d', build.id)

        with self.env.db_transaction as db:
            for step in BuildStep.select(self.env, build=build.id):
                step.delete()

            build.slave = None
            build.started = 0
            build.stopped = 0
            build.last_activity = 0
            build.status = Build.PENDING
            build.slave_info = {}
            build.update()

            Attachment.delete_all(self.env, 'build', build.resource.id)

        #commit

        req.redirect(req.href.build(build.config))

    def _render_log(self, req, build, formatters, step):
        items = []
        for log in BuildLog.select(self.env, build=build.id, step=step.name):
            for level, message in log.messages:
                for format in formatters:
                    message = format(step, log.generator, level, message)
                items.append({'level': level, 'message': message})
        return items

    def _render_reports(self, req, config, build, summarizers, step):
        reports = []
        for report in Report.select(self.env, build=build.id, step=step.name):
            summarizer = summarizers.get(report.category)
            if summarizer:
                tmpl, data = summarizer.render_summary(req, config, build,
                                                        step, report.category)
                reports.append({'category': report.category,
                                'template': tmpl, 'data': data})
            else:
                tmpl = data = None
        return reports


class ReportChartController(Component):
    implements(IRequestHandler)

    generators = ExtensionPoint(IReportChartGenerator)

    # IRequestHandler methods
    def match_request(self, req):
        match = re.match(r'/build/([\w.-]+)/chart/(\w+)', req.path_info)
        if match:
            req.args['config'] = match.group(1)
            req.args['category'] = match.group(2)
            return True

    def process_request(self, req):
        category = req.args.get('category')
        config = BuildConfig.fetch(self.env, name=req.args.get('config'))

        for generator in self.generators:
            if category in generator.get_supported_categories():
                tmpl, data = generator.generate_chart_data(req, config,
                                                           category)
                break
        else:
            raise TracError('Unknown report category "%s"' % category)

        data['dumps'] = json.to_json

        return tmpl, data, 'text/plain'


class SourceFileLinkFormatter(Component):
    """Detects references to files in the build log and renders them as links
    to the repository browser.
    """

    implements(ILogFormatter)

    _fileref_re = re.compile(r'(?P<prefix>-[A-Za-z])?(?P<path>[\w.-]+(?:[\\/][\w.-]+)+)(?P<line>:\d+)?')

    def get_formatter(self, req, build):
        """Return the log message formatter function."""
        config = BuildConfig.fetch(self.env, name=build.config)
        repos_name, repos, repos_path = get_repos(self.env, config.path,
                                                  req.authname)
        href = req.href.browser
        cache = {}

        def _replace(m):
            filepath = posixpath.normpath(m.group('path').replace('\\', '/'))
            if not cache.get(filepath) is True:
                parts = filepath.split('/')
                path = ''
                for part in parts:
                    path = posixpath.join(path, part)
                    if path not in cache:
                        try:
                            full_path = posixpath.join(config.path, path)
                            full_path = posixpath.normpath(full_path)
                            if full_path.startswith(config.path + "/") \
                                        or full_path == config.path:
                                repos.get_node(full_path,
                                               build.rev)
                                cache[path] = True
                            else:
                                cache[path] = False
                        except TracError:
                            cache[path] = False
                    if cache[path] is False:
                        return m.group(0)
            link = href(config.path, filepath)
            if m.group('line'):
                link += '#L' + m.group('line')[1:]
            return Markup(tag.a(m.group(0), href=link))

        def _formatter(step, type, level, message):
            buf = []
            offset = 0
            for mo in self._fileref_re.finditer(message):
                start, end = mo.span()
                if start > offset:
                    buf.append(message[offset:start])
                buf.append(_replace(mo))
                offset = end
            if offset < len(message):
                buf.append(message[offset:])
            return Markup("").join(buf)

        return _formatter
