#!/usr/bin/env python3
# -*- coding:utf-8 -*-

import json
from collections import defaultdict
from pathlib import Path
from textwrap import indent

import click
from clk.config import config
from clk.decorators import (argument, command, flag, group, option,
                            table_fields, table_format, use_settings)
from clk.lib import Spinner, TablePrinter, call, get_keyring
from clk.log import get_logger
from clk.types import DynamicChoice

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
@option("--private-token", help="The token to provide to the gitlab API")
@flag("--ask-token/--no-ask-token", help="Whether the token should be asked if not automatically guessed", default=True)
@option("--url", help="The url to connect to the gitlab API", default="https://gitlab.com/")
def gitlab(private_token, url, ask_token):
    "Play with gitlab"
    private_token = (private_token or get_token()
                     or (ask_token and click.prompt("token", hide_input=True, default="", show_default=False)) or None)
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
        raise click.UsageError("You must provide a group id, run the groups command to find one")
    config.gitlab.group = GitlabGroupConfig(group_id)


def sort_members(members):
    return sorted(members, key=lambda member: member.name)


@group.command()
@table_format(default='key_value')
@table_fields(choices=["id", "name"])
@flag("--only-explicit/--show-implicit", help="Don't show implicit members")
def walk_members(fields, format, only_explicit):
    """Recursively walk through all the projects showing the members per group"""
    for project in config.gitlab.group.walk_group_and_projects():
        print(f"## Project: {project.id}: {project.name}")
        explicit_members = sort_members(list(project.members.list(as_list=False)))
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
                for user in sort_members(project.members.all(all=True, as_list=False)):
                    tp.echo(user.id, user.name)


@group.command()
@table_format(default='key_value')
@table_fields(choices=["name", "members_web_url"])
def walk_project_per_member(fields, format):
    """Like walk_members, but focus on the members

For each member, show the groups that explicitly contain that member.

This might take a long time, as we need to first span the whole tree of
groups/project to have the full list of members.
    """
    project_per_member = defaultdict(list)
    LOGGER.info("This may take a few minutes, as we scan the whole tree of groups"
                " to gather all the members. Please be patient.")
    with Spinner():
        for project in config.gitlab.group.walk_group_and_projects():
            for member in project.members.list(as_list=False):
                project_per_member[f"{member.name} ({member.username})"].append(project)
    for username, groups in sorted(project_per_member.items()):
        print(f"{username}")
        for group in sorted(groups, key=lambda group: group.name):
            with TablePrinter(fields, format) as tp:
                tp.echo("  " + group.name, group.web_url + "/-/group_members")


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


@gitlab.group()
@option("--project-id", help="The id of the project to consider", required=True)
def project(project_id):
    """Manipulate project"""
    config.project_id = project_id


@project.command()
@argument("job-name", help="The name of the job to download artifacts from")
def download_artifacts(job_name):
    "Download the last artifact of project generated by the job whose name is given"
    project = config.gitlab.api.projects.get(config.project_id)
    job = next(job for job in project.jobs.list(as_list=False, scope=["success"]) if job.name == job_name)
    Path("artifacts.zip").write_bytes(job.artifacts())


@project.command()
def list_images():
    """List the docker images"""
    indentation = "  "
    g = config.gitlab
    api = g.api
    project = api.projects.get(config.project_id)
    for repository in project.repositories.list():
        print(f"{repository.path}:")
        for tag in repository.tags.list(as_list=False):
            print(indent(tag.path, indentation))


@gitlab.command()
def ipython():
    "Run an interactive python to play with gitlab"
    g = config.gitlab
    api = g.api
    import IPython
    dict_ = globals()
    dict_.update(locals())
    IPython.start_ipython(argv=[], user_ns=dict_)
