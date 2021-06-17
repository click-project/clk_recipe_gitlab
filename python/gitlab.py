#!/usr/bin/env python3
# -*- coding:utf-8 -*-

from pathlib import Path
import json

import click

from click_project.decorators import (
    argument,
    flag,
    option,
    command,
    group,
    use_settings,
    table_format,
    table_fields,
)
from click_project.lib import (
    TablePrinter,
    call,
    get_keyring,
)
from click_project.config import config
from click_project.log import get_logger
from click_project.types import DynamicChoice
from gitlab import Gitlab


LOGGER = get_logger(__name__)


class GitlabConfig:
    def __init__(self, url, private_token):
        self.api = Gitlab(url=url, private_token=private_token)


def get_token():
    res = get_keyring().get_password("click-project", "gitlab-token")
    if res:
        return json.loads(res)[1]


@group()
@option("--private-token",
        help="The token to provide to the gitlab API")
@option("--url",
        help="The url to connect to the gitlab API",
        default="https://gitlab.com/")
def gitlab(private_token, url):
    "Play with gitlab"
    private_token = (
        private_token
        or
        get_token()
    )
    config.gitlab = GitlabConfig(url, private_token)


def walk_subgroups(group):
    yield group
    for subgroup in group.subgroups.list(as_list=False):
        subgroup = config.gitlab.api.groups.get(subgroup.id)
        yield from walk_subgroups(subgroup)


def walk_projects(group):
    for group in walk_subgroups(group):
        for project in group.projects.list(as_list=False):
            yield config.gitlab.api.projects.get(project.id)


def walk_group_and_projects(group):
    for group in walk_subgroups(group):
        yield group
        for project in group.projects.list(as_list=False):
            yield config.gitlab.api.projects.get(project.id)


class GitlabGroupConfig:
    def __init__(self, group_id):
        self.group = config.gitlab.api.groups.get(group_id)

    def walk_projects(self):
        yield from walk_projects(self.group)

    def walk_group_and_projects(self):
        yield from walk_group_and_projects(self.group)


@gitlab.group()
@option("--group-id", help="The id of the group to consider")
def group(group_id):
    """Manipulate the given group"""
    if group_id is None:
        raise click.UsageError(
            "You must provide a group id, run the groups command to find one"
        )
    config.gitlab.group = GitlabGroupConfig(group_id)


@group.command()
@table_format(default='key_value')
@table_fields(choices=["id", "name"])
@flag("--only-explicit/--show-implicit", help="Don't show implicit members")
def walk_members(fields, format, only_explicit):
    """Recursively walk through all the projects showing the members per group"""
    for project in config.gitlab.group.walk_group_and_projects():
        print(f"## Project: {project.id}: {project.name}")
        explicit_members = list(project.members.list(as_list=False))
        if explicit_members:
            print("### Explicit members")
            with TablePrinter(fields, format) as tp:
                for user in explicit_members:
                    tp.echo(user.id, user.name)
        else:
            print("### No explicit members")
        if not only_explicit:
            print("### Implicit members")
            with TablePrinter(fields, format) as tp:
                for user in project.members.all(all=True, as_list=False):
                    tp.echo(user.id, user.name)


@group.command()
@table_format(default='key_value')
@table_fields(choices=["id", "name"])
@flag("--only-explicit/--show-implicit", help="Don't show implicit members")
def walk_project_members(fields, format, only_explicit):
    """Recursively walk through all the projects showing the members per group"""
    for project in config.gitlab.group.walk_projects():
        print(f"## Project: {project.id}: {project.name}")
        print("### Explicit members")
        with TablePrinter(fields, format) as tp:
            for user in project.members.list(as_list=False):
                tp.echo(user.id, user.name)
        if not only_explicit:
            print("### Implicit members")
            with TablePrinter(fields, format) as tp:
                for user in project.members.all(all=True, as_list=False):
                    tp.echo(user.id, user.name)


@gitlab.command()
@table_format(default='key_value')
@table_fields(choices=["id", "name"])
def groups(format, fields):
    """List the available groups"""
    with TablePrinter(fields, format) as tp:
        for group in config.gitlab.api.groups.list(as_list=False):
            tp.echo(group.id, group.name)


@gitlab.command()
def ipython():
    "Run an interactive python to play with gitlab"
    g = config.gitlab
    api = g.api
    import IPython
    dict_ = globals()
    dict_.update(locals())
    IPython.start_ipython(argv=[], user_ns=dict_)
