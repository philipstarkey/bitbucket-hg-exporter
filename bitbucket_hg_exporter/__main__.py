# Copyright 2019 Philip Starkey
#
# This file is part of bitbucket-hg-exporter.
# https://github.com/philipstarkey/bitbucket-hg-exporter
#
# bitbucket-hg-exporter is distributed under a custom license.
# See the LICENSE file in the GitHub repository for further details.

import argparse
import copy
import json
import getpass
import queue
import re
import requests
import threading
import time
import os
import shutil
import sys
from urllib import parse

from OpenSSL.SSL import SysCallError
from distutils.dir_util import copy_tree

bitbucket_api_url = 'https://api.bitbucket.org/2.0/'

def bb_endpoint_to_full_url(endpoint):
    return bitbucket_api_url + endpoint

def full_url_to_query(url):
    split_data = parse.urlsplit(url)
    params = parse.parse_qs(split_data.query)
    endpoint = parse.urlunsplit(list(split_data[0:3])+['',''])
    return endpoint, params

def bb_query_api(endpoint, auth, params=None):
    if not endpoint.startswith('https://'):
        endpoint = bb_endpoint_to_full_url(endpoint)
    endpoint, orig_params = full_url_to_query(endpoint)
    if params is not None:
        orig_params.update(params)
    # Catch the API limit
    retry = True
    response = None
    while retry:
        try:
            response = requests.get(endpoint, params=orig_params, auth=auth)
            retry = False
        except requests.exceptions.SSLError:
            print('API limit likely exceeded. Will retry in 5 mins...')
            time.sleep(60*5)
        except BaseException:
            # retry = False
            raise
    return response

def bbapi_json(endpoint, auth, params=None):
    response = bb_query_api(endpoint, auth, params)
    try:
        json_response = response.json()
    except BaseException:
        json_response = None

    return response.status_code, json_response

import keyring
KEYRING_SERVICES = {
    'bitbucket': 'bitbucket-to-github-exporter/bitbucket',
    'github': 'bitbucket-to-github-exporter/github',
}
import questionary as q

class MigrationProject(object):
    def __init__(self):
        self.__auth_credentials = {}
        for service in KEYRING_SERVICES:
            self.__auth_credentials[service] = {}

        self.__settings = {
            'project_name': '',
            'project_path': '',
            'master_bitbucket_username': '',
            'bitbucket_repo_owner': '',
            'bitbucket_repo_project': None,
            'bb_repositories_to_export': [],
            'backup_issues': True,
            'backup_pull_requests': True,
            'backup_commit_comments': True,
            'generate_static_issue_pages': True,
            'generate_static_pull_request_pages': True,
            'generate_static_commit_comments_pages': True,

            'bitbucket_api_download_complete': False,
            'bitbucket_api_URL_replace_complete': False,
            'bitbucket_hg_download_complete': False,
        }

        p = argparse.ArgumentParser()
        p.add_argument('--load', action='store_true')
        p.add_argument('--storage-dir')
        p.add_argument('--project-name')
        arguments = p.parse_args()

        choices = {"Start new project":0, "Load project":1}
        if arguments.load:
            response=list(choices.keys())[1]
        else:
            # prompt for new/load
            response = q.select("What do you want to do?", choices=choices.keys()).ask()

        if choices[response] == 0:
            self.__start_project()
        elif choices[response] == 1:
            kwargs = {}
            if arguments.storage_dir is not None:
                kwargs['location'] = arguments.storage_dir
            if arguments.project_name is not None:
                kwargs['project'] = arguments.project_name
            self.__load_project(**kwargs)
        else:
            raise RuntimeError('Unknown option selected')

    def __load_project(self, location=os.getcwd(), project=None):
        project_found = False
        first_run = True
        while not project_found:
            if not first_run or location == os.getcwd():
                location = q.text("Where is the project folder located?", default=location).ask()
            if not first_run or project is None:
                project_name = q.select("Select a project to load?", choices=os.listdir(location)).ask()
            elif first_run and project is not None:
                project_name = project

            path = os.path.join(location, project_name, 'project.json')
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        self.__settings.update(json.load(f))
                    project_found = True
                except BaseException:
                    print('Could not load project.json file in {}. It may be corrupted. Please check the formatting and try again'.format(path))
            else:
                print('Could not find {}. Please select a differet folder.'.format(path))

            first_run = False

        # make sure we have a password/token or ask for it
        self.__get_password('bitbucket', self.__settings['master_bitbucket_username'], silent=False)

        self.__confirm_project_settings(load=True)

    def __start_project(self):
        # Get the project name and save loction
        while not self.__get_project_name():
            print('Could not create a migration project. Please ensure you have write permissions at the specified location and that the project name is unique')

        # Get the Information on the BitBucket repo(s) to migrate
        self.__get_bitbucket_info()

        # find out what we should be saving
        self.__get_backup_options()

        # TODO: questions about import to GitHub

        self.__confirm_project_settings()

    def __confirm_project_settings(self, load=False):
        # confirm settings before beginning
        while not self.__print_project_settings():
            choices = {
                "Change primary BitBucket credentials":0, 
                "Change BitBucket repositories to export":1,
                "Change export settings":2,
            }
            if load:
                choices["Load different project"] = 3
            response = q.select("What would you like to change?", choices=choices.keys()).ask()
            if choices[response] == 0:
                self.__get_master_bitbucket_credentials(force_new_password=True)
            elif choices[response] == 1:
                while not self.__get_bitbucket_repositories():
                    pass
            elif choices[response] == 2:
                self.__get_backup_options()
            elif choices[response] == 3:
                self.__load_project()
            else:
                raise RuntimeError('Unknown option selected')

        # save the project
        self.__save_project_settings()
        
        # prompt to start project
        print('Project configuration saved!')
        #TODO: make resume have nicer text prompts
        choices = {
            "Start export":0, 
            "Exit":1,
        }
        response = q.select("What would you like to do?", choices=choices.keys()).ask()
        if choices[response] == 0:
            owner = self.__settings['bitbucket_repo_owner']
            creds = (self.__settings['master_bitbucket_username'], self.__get_password('bitbucket', self.__settings['master_bitbucket_username']))
            exporter = BitBucketExport(owner, creds, copy.deepcopy(self.__settings))
            if not self.__settings['bitbucket_api_download_complete'] or not self.__settings['bitbucket_api_URL_replace_complete']:
                exporter.backup_api()
                self.__settings['bitbucket_api_download_complete'] = True
                self.__save_project_settings()
            # rewrite URLS to reference the downloaded ones
            if not self.__settings['bitbucket_api_URL_replace_complete']:
                exporter.make_urls_relative()
                self.__settings['bitbucket_api_URL_replace_complete'] = True
                self.__save_project_settings()
            # copy the gh-pages template to the project directory
            do_copy = True
            if os.path.exists(os.path.join(self.__settings['project_path'], 'gh-pages', 'index.html')):
                do_copy = q.confirm('Overwrite HTML app for GitHub pages site with latest version?').ask()
                if do_copy:
                    # delete old version
                    try:
                        os.remove(os.path.join(self.__settings['project_path'], 'gh-pages', 'index.html'))
                    except BaseException:
                        pass
                    try:
                        shutil.rmtree(os.path.join(self.__settings['project_path'], 'gh-pages', 'ng'))
                    except BaseException:
                        pass
            if do_copy:
                copy_tree(os.path.join(os.path.dirname(__file__), 'gh-pages-template'), os.path.join(self.__settings['project_path'], 'gh-pages'))

            # write out a list of downloaded repos and a link to their top level JSON file
            with open(os.path.join(self.__settings['project_path'], 'gh-pages', 'repos.json'), 'w') as f:
                data = {}
                for repository in self.__settings['bb_repositories_to_export']:
                    data[repository['slug']] = {
                        'project_file': 'data/repositories/{owner}/{repo}.json'.format(owner=self.__settings['bitbucket_repo_owner'], repo=repository['slug']),
                        'project_path': 'data/repositories/{owner}/{repo}/'.format(owner=self.__settings['bitbucket_repo_owner'], repo=repository['slug']),
                    }
                json.dump(data, f, indent=4)
            # write out a site pages list

        elif choices[response] == 1:
            sys.exit(0)
        else:
            raise RuntimeError('Unknown option selected')

    def __save_project_settings(self):
        with open(os.path.join(self.__settings['project_path'], 'project.json'), 'w') as f:
            json.dump(self.__settings, f, indent=4)

    def __get_project_name(self):
        self.__settings['project_name'] = q.text("Enter name for this migration project:").ask()
        location = q.text("Enter a path to save this project in:", default=os.getcwd()).ask()
        self.__settings['project_path'] = os.path.join(location, self.__settings['project_name'])

        # create the project directory, ignore error is the directory structure
        # already exists, but return False on any other errors
        try:
            os.makedirs(self.__settings['project_path'])
        except FileExistsError:
            pass
        except BaseException:
            return False

        # Make sure the path exists, that it is a directory, and that the 
        # directory is empty
        if os.path.exists(self.__settings['project_path']) and os.path.isdir(self.__settings['project_path']) and not os.listdir(self.__settings['project_path']):
            return True
        else:
            return False

    def __get_bitbucket_info(self):
        # Get bitbucket username, password
        self.__get_master_bitbucket_credentials()

        # get a list of bitbucket repositories to save
        while not self.__get_bitbucket_repositories():
            pass

        # TODO: get additional credentials to bypass BitBucket API rate limit

    def __get_bitbucket_repositories(self):
        # Get BitBucket repo/project/team/user that we want to back up
        choices = {"User":0, "Team":1, "Project within a team":2}
        response = q.select("Where are your repositories located?", choices=choices.keys()).ask()
        if choices[response] == 0:
            self.__settings['bitbucket_repo_owner'] = q.text("Who is the user that owns the repository(ies)?", default=self.__settings['bitbucket_repo_owner'] if self.__settings['bitbucket_repo_owner'] else self.__settings['master_bitbucket_username']).ask()
            self.__settings['bitbucket_repo_project'] = None
        elif choices[response] in [1,2]:
            self.__settings['bitbucket_repo_owner'] = q.text("What is the team name that owns the repository(ies)?", default=self.__settings['bitbucket_repo_owner']).ask()
            if choices[response] == 2:
                self.__settings['bitbucket_repo_project'] = q.text("What is the project key (not name) within the team?", default=self.__settings['bitbucket_repo_project'] if self.__settings['bitbucket_repo_project'] is not None else '').ask()
            else:
                self.__settings['bitbucket_repo_project'] = None
        else:
            raise RuntimeError('Unknown option selected')

        # Get a list of all hg repositories for this user/team and filter by project if+ relevant
        auth = (self.__settings['master_bitbucket_username'], self.__get_password('bitbucket', self.__settings['master_bitbucket_username']))
        status, json_response = bbapi_json('repositories/{}'.format(self.__settings['bitbucket_repo_owner']), auth, {'q':'scm="hg"', 'pagelen':100})

        bb_repositories = []
        def recursively_process_repositories(status, json_response, bb_repositories):
            if status == 200 and json_response is not None:
                # process repositories
                bb_repositories.extend(json_response['values'])
                while 'next' in json_response:
                    status, json_response = bbapi_json(json_response['next'], auth, {'q':'scm="hg"', 'pagelen':100})
                    return recursively_process_repositories(status, json_response, bb_repositories)
            else:
                return False

            return True
        
        success = recursively_process_repositories(status, json_response, bb_repositories)
        if not success:
            print('Could not get a list of repositories from BitBucket. Please check the specified repository owner (user/team) is correct and try again.')
            return False

        # if we have a project, filter the repository list by those
        if self.__settings['bitbucket_repo_project'] is not None:
            bb_repositories = [repo for repo in bb_repositories if repo['project']['key'] == self.__settings['bitbucket_repo_project']]
        if len(bb_repositories) == 0:
            print('There were no mercurial repositories found in the specified location. Please try again.')
            return False

        # list the repositories so they can be selected for migration
        choices = [q.Choice(repo['name'], checked=True if not self.__settings['bb_repositories_to_export'] else repo in self.__settings['bb_repositories_to_export']) for repo in bb_repositories]
        response = q.checkbox('Select repositories to export', choices=choices).ask()

        if len(response) == 0:
            print('You did not select any repositories to export. Please try again.')

        # save the list of repositories we are going to export
        self.__settings['bb_repositories_to_export'] = [repo for repo in bb_repositories if repo['name'] in response]

        return True

    def __get_backup_options(self):
        self.__settings['backup_issues'] = q.confirm('Backup BitBucket issues as JSON files?', default=self.__settings['backup_issues']).ask()
        if self.__settings['backup_issues']:
            self.__settings['generate_static_issue_pages'] = q.confirm('Generate new issue HTML pages for upload to a website?', default=self.__settings['generate_static_issue_pages']).ask()
        else:
            self.__settings['generate_static_issue_pages'] = False

        self.__settings['backup_pull_requests'] = q.confirm('Backup BitBucket pull requests as JSON files?', default=self.__settings['backup_pull_requests']).ask()
        if self.__settings['backup_pull_requests']:
            self.__settings['generate_static_pull_request_pages'] = q.confirm('Generate new pull request HTML pages for upload to a website?', default=self.__settings['generate_static_pull_request_pages']).ask()
        else:
            self.__settings['generate_static_pull_request_pages'] = False

        self.__settings['backup_commit_comments'] = q.confirm('Backup BitBucket commit comments as JSON files?', default=self.__settings['backup_commit_comments']).ask()
        if self.__settings['backup_commit_comments']:
            self.__settings['generate_static_commit_comments_pages'] = q.confirm('Generate new commit comments HTML pages for upload to a website?', default=self.__settings['generate_static_commit_comments_pages']).ask()
        else:
            self.__settings['generate_static_commit_comments_pages'] = False

    def __print_project_settings(self):
        print('Project settings:')
        print('    Name: {}'.format(self.__settings['project_name']))
        print('    Path: {}'.format(self.__settings['project_path']))
        print('    BitBucket username: {}'.format(self.__settings['master_bitbucket_username']))
        print('    Repositories to export:')
        for repo in self.__settings['bb_repositories_to_export']:
            print('        {}'.format(repo['full_name']))
        print('    Backup BitBucket issues: {}'.format(str(self.__settings['backup_issues'])))
        print('        Generate HTML pages: {}'.format(str(self.__settings['generate_static_issue_pages'])))
        print('    Backup BitBucket pull requests: {}'.format(str(self.__settings['backup_pull_requests'])))
        print('        Generate HTML pages: {}'.format(str(self.__settings['generate_static_pull_request_pages'])))
        print('    Backup BitBucket commit comments: {}'.format(str(self.__settings['backup_commit_comments'])))
        print('        Generate HTML pages: {}'.format(str(self.__settings['generate_static_commit_comments_pages'])))
        
        response = q.confirm('Is this correct?').ask()
        return response

    def __get_master_bitbucket_credentials(self, force_new_password=False):
        self.__settings['master_bitbucket_username'] = self.__get_bitbucket_credentials(self.__settings['master_bitbucket_username'], force_new_password)

    def __get_bitbucket_credentials(self, username, force_new_password=False):
        # Get username
        username = q.text("What is your BitBucket username?", default=username).ask()

        # Get password/token
        self.__get_password('bitbucket', username, silent=False, force_new_password=force_new_password)
        
        return username

    def __get_github_credentials(self, username, force_new_password=False):
        # Get username
        username = q.text("What is your GitHub username?", default=username).ask()

        # Get password/token
        self.__get_password('github', username, silent=False, force_new_password=force_new_password)
        
        return username

    def __get_password(self, service, username, silent=True, force_new_password=False):
        if not force_new_password:
            # TODO: Look for saved passwords from other applications? (e.g. TortoiseHg)
            password = self.__auth_credentials[service].get(username, None) or keyring.get_password(KEYRING_SERVICES[service], username)
            
            if password is not None:
                # check the password works
                status, _ = bbapi_json('user', (username, password))
                if status == 200:
                    # If we are just wanting the password, then return it
                    if silent:
                        return password
                    # if we are asking the user for their credentials, ask them if they are happy to use the one we found
                    use = q.confirm('Existing credential found. Do you wish to use it?')
                    if use:
                        return password

        # If there is no password saved, then we can't be silent!

        # Get password
        not_authenticated = True
        while not_authenticated:
            choices = {"Password":0, "Token":1}
            response = q.select("Authenticate user '{}' using password or token?".format(username), choices=choices.keys()).ask()
            if choices[response] == 0:
                password = q.password("Enter your password:").ask()
            elif choices[response] == 1:
                password = q.text("Enter your access token:").ask()
            else:
                raise RuntimeError('Unknown option selected')
            
            # check the password works
            status, _ = bbapi_json('user', (username, password))
            if status == 200:
                not_authenticated = False
            else:
                print('Could not authenticate. Please check the password and try again.')

        # save credentials in RAM
        self.__auth_credentials[service][username] = password

        # save credentials in keyring?
        save = q.confirm('Save credentials in operating system keyring?').ask()
        if save:
            keyring.set_password(KEYRING_SERVICES[service], username, password)

        return password
        

prog = re.compile(r'\"{}(.*?)\"'.format(bitbucket_api_url), re.MULTILINE)



class BitBucketExport(object):
    #
    # This code is terrible and is not going to do what I want.
    # We can't parallelise the download of JSON data if we want to be able to 
    # resume it without processing every saved JSON file
    #
    def __init__(self, owner, credentials, options):
        self.__owner = owner
        self.__credentials = credentials
        self.__options = options

        self.__save_path = os.path.join(options['project_path'], 'bitbucket_data_raw')
        self.__save_path_relative = os.path.join(options['project_path'], 'gh-pages', 'data')

        self.__tree = []
        self.__current_tree_location = ()

        self.tree_new_level()

        # TODO: Save attachments - DONE
        #       Guess file extension from mime type (see https://stackoverflow.com/questions/29674905/convert-content-type-header-into-file-extension)
        #       Save downloads
        #       Ignore endpoint "issue/<num>/attachments/<file>" in the JSON function (they are processed there which fails as well as the download file function which succeeds)
        #       checkout wiki
        #       checkout repo
        #       save issue changelist which isn't linked to from other JSON files for some reason so is missed by the code below - DONE


    def backup_api(self):
        for repository in self.__options['bb_repositories_to_export']:
            # this is a bit of a hack but whatever!
            self.__repository = repository['slug']
            self.__backup_api()

    def __backup_api(self):    
        self.file_download_regexes = [
            re.compile(r'\"(https://bitbucket\.org/repo/(?:[a-zA-Z0-9]+)/images/(?:.+?))\\\"', re.MULTILINE), # images in HTML
            re.compile(r'\"(https://pf-emoji-service--cdn\.(?:[a-zA-Z0-9\-]+)\.prod\.public\.atl-paas\.net/(?:.+?))\\\"', re.MULTILINE), # emojis
            re.compile(r'\"(https://secure.gravatar.com/avatar/(?:.+?))\"', re.MULTILINE), # avatars
            re.compile(r'\"(https://bytebucket\.org/(?:.+?))\"', re.MULTILINE), # other things (like language avatars)
            # re.compile(r'\"(https://bytebucket\.org/(?:.+?))\"', re.MULTILINE), # TODO: downloads
            re.compile(r'\"(https://api\.bitbucket\.org/2\.0/repositories/{owner}/{repo}/issues/(?:\d+)/attachments/(?:.+?))\"'.format(owner=self.__owner, repo=self.__repository), re.MULTILINE), # attachments
        ]

        # TODO: probably want to save some of these...the question is how far do we go down the tree.
        #       for example, users link to other repos which then result in you saving data for every 
        #       repo for every user, etc, etc.
        ignore_rules = [
            {'type': 'in', 'not':False, 'string':'repositories/{owner}/{repo}/patch'.format(owner=self.__owner, repo=self.__repository)},
            # {'type': 'in', 'not':False, 'string':'repositories/{owner}/{repo}/commit'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'in', 'not':False, 'string':'repositories/{owner}/{repo}/diff'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'in', 'not':False, 'string':'repositories/{owner}/{repo}/src'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'in', 'not':False, 'string':'repositories/{owner}/{repo}/filehistory'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'in', 'not':False, 'string':'repositories/{owner}/{repo}/downloads'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'startswith', 'not':True, 'string':'repositories/{owner}/{repo}'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/issues/import'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/issues/export'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/hooks'.format(owner=self.__owner, repo=self.__repository)},
            # Get the list of commits, but not individual commit JSON files
            # {'type': 'endswith', 'not':True, 'string':'repositories/{owner}/{repo}/commits/'.format(owner=self.__owner, repo=self.__repository)},
            {'type': 'endswith', 'not':False, 'string':'/approve'},
            {'type': 'endswith', 'not':False, 'string':'/decline'},
            {'type': 'endswith', 'not':False, 'string':'/merge'},
            {'type': 'endswith', 'not':False, 'string':'/vote'},
            {'type': 'endswith', 'not':False, 'string':'/watch'},
        ]

        pr_ignores = [
            # {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/commit/'.format(owner=self.__owner, repo=self.__repository)},
            # {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/issues'.format(owner=self.__owner, repo=self.__repository)},
        ]

        issue_ignores = [
            # {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/pullrequests/'.format(owner=self.__owner, repo=self.__repository)},
            # {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/commit/'.format(owner=self.__owner, repo=self.__repository)},
        ]

        commit_comments_ignores = [
            # {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/issues/'.format(owner=self.__owner, repo=self.__repository)},
            # {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/pullrequests/'.format(owner=self.__owner, repo=self.__repository)},
        ]

        rewrite_rules = [
            # special case for pull requests
            {
                'endpoint_match':['repositories/{owner}/{repo}/pullrequests'.format(owner=self.__owner, repo=self.__repository)], 
                'rewrites':[
                    {
                        'params_match':{'state':None}, 
                        'params_to_update':{'state': ['MERGED', 'OPEN', 'SUPERSEDED', 'DECLINED']},
                    },
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 50},
                    },
                    {
                        'params_match':{'page':None}, 
                        'params_to_update':{'page': 1},
                    }
                ]  
            },
            # endpoints that take a max pagelen of 50 but don't have a page by default
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/activity(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    'repositories/{owner}/{repo}/pullrequests/activity'.format(owner=self.__owner, repo=self.__repository),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 50},
                    },
                ]  
            },
            # endpoints that take a max pagelen of 100 but don't have a page by default
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}/issues\/(\d+)\/changes(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/commits(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    'repositories/{owner}/{repo}/refs/tags'.format(owner=self.__owner, repo=self.__repository),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 100},
                    },
                ]  
            },
            # endpoints that take a max pagelen of 100
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/comments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/statuses(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/issues\/(\d+)\/attachments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/issues\/(\d+)\/comments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/commits\/.*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/commit\/(.+?)\/comments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/commit\/(.+?)\/statuses(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    'repositories/{owner}/{repo}/commits'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/components'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/forks'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/issues'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/milestones'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/refs'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/refs/branches'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/versions'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/watchers'.format(owner=self.__owner, repo=self.__repository),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 100},
                    },
                    {
                        'params_match':{'page':None}, 
                        'params_to_update':{'page': 1},
                    }
                ]  
            },
            # endpoints that take a max pagelen of 5000
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}\/diffstat\/.*'.format(owner=self.__owner, repo=self.__repository)),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 5000},
                    },
                    {
                        'params_match':{'page':None}, 
                        'params_to_update':{'page': 1},
                    }
                ]  
            },
        ]

        # pull requests
        if self.__options['backup_pull_requests']:
            self.get_and_save_json('https://api.bitbucket.org/2.0/repositories/{owner}/{repo}'.format(owner=self.__owner, repo=self.__repository), ignore_rules + pr_ignores, rewrite_rules)
            self.tree_increment_level()
            # self.make_urls_relative()

    @property
    def current_tree_location(self):
        return self.__current_tree_location

    @current_tree_location.setter
    def current_tree_location(self, value):
        # TODO: write this to a file along with the tree
        #       so we can resume it if it fails part way through
        self.__current_tree_location = value

    def tree_new_level(self):
        self.current_tree_location += (0,)

    def tree_finished_level(self):
        self.current_tree_location = self.current_tree_location[:-1]

    def tree_increment_level(self):
        self.current_tree_location = (*self.current_tree_location[:-1], self.current_tree_location[-1]+1)

    def rewrite_url(self, endpoint, params, rules):
        params = copy.deepcopy(params)
        for rule in rules:
            endpoint_matches = False
            for endpoint_match in rule['endpoint_match']:
                if (isinstance(endpoint_match, str) and endpoint == endpoint_match) or (isinstance(endpoint_match, re.Pattern) and endpoint_match.findall(endpoint)):
                    endpoint_matches = True
                    break
            if endpoint_matches:
                for rewrite in rule['rewrites']:
                    do_rewrite = True
                    for match_param_name, match_param_value in rewrite['params_match'].items():
                        if match_param_value is None:
                            if match_param_name in params and params[match_param_name] != match_param_value:
                                do_rewrite = False
                                break
                        else:
                            if match_param_name not in params or params[match_param_name] != match_param_value:
                                do_rewrite = False
                                break

                    if do_rewrite:
                        for rewrite_param_name, rewrite_param_value in rewrite['params_to_update'].items():
                            if rewrite_param_value is None and rewrite_param_name in params:
                                del params[rewrite_param_name]
                            else:
                                if isinstance(rewrite_param_value, (list, dict)):
                                    rewrite_param_value = copy.deepcopy(rewrite_param_value)
                                params[rewrite_param_name] = rewrite_param_value

        return endpoint, params

    def download_file(self, base_url):
        # convert url to save path
        # remove '/' before the decode as the ones that exist prior to the decode as real characters
        #  (aka the '/' in the address, not query params) shouldn't be removed
        corrected_url_path = parse.unquote(base_url.replace(r'%2F', r'')).replace(bitbucket_api_url, '').replace('https://', '').replace('http://', '')
        special_chars = ['?', ':', '\\', '*','<', '>', '"', '|']
        for c in special_chars:
            corrected_url_path = corrected_url_path.replace(c,'')
        save_path = os.path.join(self.__save_path, corrected_url_path)

        # save this URL in the tree
        tree = self.__tree
        for i in self.current_tree_location[:-1]:
            tree = tree[i]['children']
        tree.append({'url': base_url, 'rewritten_url': base_url, 'endpoint_path':save_path, 'already_processed': False, 'children': []})


        # don't download if it is already downloaded
        if os.path.exists(save_path):
            # mark as already processed
            tree[-1]['already_processed'] = True
            return

        # create the dir structure
        head, _ = os.path.split(save_path)
        try:
            os.makedirs(head)
        except FileExistsError:
            pass

        r = requests.get(base_url, stream=True)
        with open(save_path, 'wb') as fd:
            for chunk in r.iter_content(1024**2): # 1Mb chunk size
                fd.write(chunk)

    def get_and_save_json(self, base_url, ignore_rules, rewrite_rules):
        # TODO: handle resume from partial download 

        endpoint, params = full_url_to_query(base_url)
        endpoint = endpoint.replace(bitbucket_api_url, '')
        endpoint = endpoint.split('?')[0]
        # rewrite URL
        rewritten_endpoint, rewritten_params = self.rewrite_url(endpoint, params, rewrite_rules)

        endpoint_path = os.path.join(self.__save_path, rewritten_endpoint)
        
        encoded_rewritten_params = parse.urlencode(rewritten_params, doseq=True)
        endpoint_path += encoded_rewritten_params
        endpoint_path += ".json"

        # create new URL to query
        rewritten_base_url = bitbucket_api_url + rewritten_endpoint
        if encoded_rewritten_params:
            rewritten_base_url += '?' + encoded_rewritten_params

        # save this URL in the tree
        tree = self.__tree
        for i in self.current_tree_location[:-1]:
            tree = tree[i]['children']
        tree.append({'url': base_url, 'rewritten_url': rewritten_base_url, 'endpoint_path':endpoint_path, 'already_processed': False, 'children': []})

        # create the dir structure
        head, _ = os.path.split(endpoint_path)
        try:
            os.makedirs(head)
        except FileExistsError:
            pass

        if os.path.exists(endpoint_path):
            # load the file
            response = DummyResponse(endpoint_path)
            if response.already_processed:
                # mark as already processed
                tree[-1]['already_processed'] = True
                return
        else:
            response = bb_query_api(rewritten_base_url, auth=self.__credentials)
        
        # print some debug info        
        print(self.current_tree_location, base_url)
        if rewritten_base_url != base_url:
            print(self.current_tree_location, rewritten_base_url)

        if response.status_code == 200:
            # save the data
            try:
                json_data = response.json()
            except BaseException:
                print('Not a JSON response, ignoring')
                print('     original endpoint:', base_url)
                print('    rewritten endpoint:', rewritten_base_url)
                # print('    data:', response.text)
                return
        
            with open(endpoint_path, 'w') as f:
                json.dump(json_data, f)

            self.tree_new_level()

            # get the other pages
            if "next" in json_data:
                self.get_and_save_json(json_data['next'], ignore_rules, rewrite_rules)
                self.tree_increment_level()

            # download any files references
            for compiled_regex in self.file_download_regexes:
                results = compiled_regex.findall(response.text)
                for result in results:
                    try:
                        print('downloading file: {}'.format(result))
                        self.download_file(result)
                        self.tree_increment_level()
                    except BaseException:
                        print('Failed to download file {}'.format(result))
                        raise

            # find all the other referenced API endpoints in this data and collect them too
            results = prog.findall(response.text)
            for result in results:
                # hack because nothing references issue/<num>/changes for some reason
                issue_pattern = r'repositories/{}/{}/issues/(\d+)$'.format(self.__owner, self.__repository)
                matches = re.match(issue_pattern, result)
                if matches:
                    self.get_and_save_json(bb_endpoint_to_full_url(result+'/changes'), ignore_rules, rewrite_rules)
                    self.tree_increment_level()

                skip = False
                for rule in ignore_rules:
                    if rule['type'] == 'in':
                        if rule['not']:
                            skip = rule['string'] not in result
                        else:
                            skip = rule['string'] in result
                    elif rule['type'] == 'startswith':
                        if rule['not']:
                            skip = not result.startswith(rule['string'])
                        else:
                            skip = result.startswith(rule['string'])
                    elif rule['type'] == 'endswith':
                        if rule['not']:
                            skip = not result.endswith(rule['string'])
                        else:
                            skip = result.endswith(rule['string'])

                    if skip:
                        break

                if skip:
                    continue

                self.get_and_save_json(bb_endpoint_to_full_url(result), ignore_rules, rewrite_rules)
                self.tree_increment_level()
            
            self.tree_finished_level()

        elif response.status_code == 401:
            print("ERROR: Access denied for endpoint {endpoint}. No data was saved. Check your credentials and access permissions.".format(endpoint=rewritten_endpoint))
        elif response.status_code == 404:
            print("ERROR: Repository {repo} doesn't exist for endpoint {endpoint}".format(endpoint=rewritten_endpoint, repo=self.__repository))
        else:
            print("ERROR: Unexpected response code {code} for endpoint {endpoint}".format(code=response.status_code, endpoint=rewritten_endpoint))

    def make_urls_relative(self, tree=None):
        # tree.append({'url': base_url, 'rewritten_url': rewritten_base_url, 'endpoint_path':endpoint_path, 'already_processed': False, 'children': []})
        
        if tree is None:
            tree = self.__tree

        for item in tree:
            # get new path
            new_path = item['endpoint_path'].replace(self.__save_path, self.__save_path_relative)
            head, _ = os.path.split(new_path)
            try:
                os.makedirs(head)
            except FileExistsError:
                pass

            skip_file = False
            # ignore if new path already converted
            if os.path.exists(new_path):
                skip_file = True
            # ignore if file doesn't exist
            if not os.path.exists(item['endpoint_path']):
                skip_file = True

            if not skip_file:
                # if it is a JSON file
                if new_path.endswith('.json'):
                    # open file
                    # print('processing', item['endpoint_path'])
                    with open(item['endpoint_path'], 'r') as f:
                        data = f.read()

                    # iterate over children and replace URLs
                    for child in item['children']:
                        # print('replacing', child['url'], 'with', child['endpoint_path'].replace(r'\\', '/').replace(r'\','/'))
                        new_url = child['endpoint_path'].replace(self.__save_path, 'data').replace('\\\\', '/').replace('\\','/')
                        data = data.replace('"{}"'.format(child['url']), '"{}"'.format(new_url)) # JSON value
                        data = data.replace(r'\"{}\"'.format(child['url']), r'\"{}\"'.format(new_url)) # escaped HTML image src in JSON
                        data = data.replace('![]({})'.format(child['url']), '![]({})'.format(new_url)) # markdown image format

                    # save file
                    with open(new_path, 'w') as f:
                        f.write(data)
                # if it is a binary file
                else:
                    shutil.copyfile(item['endpoint_path'], new_path)

            # recurse over children
            self.make_urls_relative(item['children'])

class DummyResponse(object):
    cache = {}

    def __init__(self, path):
        if getattr(self, 'already_processed', None) is not None:
            return
        self.__path = path
        self.status_code = 200
        self.already_processed = False

    def json(self):
        with open(self.__path, 'r') as f:
            return json.load(f)

    @property
    def text(self):
        with open(self.__path, 'r') as f:
            return f.read()

    def __new__(cls, path, *args, **kwargs):
        existing = DummyResponse.cache.get(path, None)
        if existing is not None:
            # print('ignoring',path)
            existing.already_processed = True
            return existing
        obj = super(DummyResponse, cls).__new__(cls)
        DummyResponse.cache[path] = obj
        return obj
        
if __name__ == "__main__":
    project = MigrationProject()