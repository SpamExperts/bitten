# -*- coding: utf-8 -*-
# Author: John Hampton <pacopablo@pacopablo.com>
from bitten import __multirepos__
from trac.versioncontrol.api import RepositoryManager
from trac.resource import Resource

__all__ = [
    'get_repos',
    'get_chgset_resource'
]

def get_repos(env, path, authname):
    """ Returns a tuple containing repository name, object, and path.

    Handles differentiating between Trac with and without multirepos support.
    If multirepos support is not present, the name is '(default)'.
    If multirepos support is present, the path is relative to the returned
    repository
    """
    global __multirepos__
    if __multirepos__ is None:
        # 23 is the schema version where multirepos support was added
        __multirepos__ = env.get_version() >= 23

    repos_name = '(default)'
    repos_path = path
    if __multirepos__:
        repos_name, repos, repos_path = RepositoryManager(env
                                                ).get_repository_by_path(path)
    else:
        repos = env.get_repository(authname=authname)
        assert repos, 'No "(default)" Repository: Add a repository or alias ' \
                       'named "(default)" to Trac.'
    return (repos_name, repos, repos_path)


def get_chgset_resource(env, repos_name, rev):
    """ Returns a resource representing the changeset specified.

    Handles differentiating between trac with and without multirepos support.
    """
    global __multirepos__

    if __multirepos__:
        repos = RepositoryManager(env).get_repository(repos_name)
    else:
        repos = env.get_repository(authname=None)
    chgset_resource = Resource('changeset', rev, parent=repos.resource)
    return chgset_resource

def display_rev(repos, rev):
    """ Return the display_rev if available, or regular normalized_rev for
    older Trac versions.
    """
    if hasattr(repos, 'display_rev'):
        return repos.display_rev(rev)
    return repos.normalize_rev(rev)

def get_resource_path(resource):
    """ Return the full path for the resource (with or without a parent).
    """
    return ("%s/%s" % (resource.parent and resource.parent.id or '',
                                resource.id.lstrip('/'))).lstrip('/')
