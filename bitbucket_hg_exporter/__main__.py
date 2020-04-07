# Copyright 2019 Philip Starkey
#
# This file is part of bitbucket-hg-exporter.
# https://github.com/philipstarkey/bitbucket-hg-exporter
#
# bitbucket-hg-exporter is distributed under the GPLv3.
# See the LICENSE file in the GitHub repository for further details.

import argparse
from collections import OrderedDict
import copy
import datetime
import gc
import getpass
import html
import json
import queue
import re
import requests
import threading
import time
import os
import shutil
import subprocess
import sys
from urllib import parse

#from OpenSSL.SSL import SysCallError
from distutils.dir_util import copy_tree

from . import hg2git
from . import __version__ as software_version 
from .issue_migrate import import_issues_to_github

bitbucket_api_url = 'https://api.bitbucket.org/2.0/'
github_api_url = 'https://api.github.com/'


def pad_message(msg):
    width = shutil.get_terminal_size()[0]
    msg += " "*(width-len(msg)%width-1)
    return msg

# setup signal handling to terminate threads nicely when
# this process is terminated
import signal
ABORT_EVENT = threading.Event()
_REGISTERED_THREADS = {}
_previous_sigint = signal.getsignal(signal.SIGINT)
_previous_sigterm = signal.getsignal(signal.SIGTERM)
# _previous_sigkill = signal.getsignal(signal.SIGKILL)
def _kill_threads_nicely(signum, frame):
    print(pad_message('Signal received. Attempting to end threads nicely (this may take up to 2 seconds per thread)'))
    ABORT_EVENT.set()
    # Wait for threads to end with a timeout...
    for name, thread in _REGISTERED_THREADS.items():
        if thread.is_alive():
            thread.join(2)
            if thread.is_alive():
                print(pad_message('WARNING: Thread {} did not terminate nicely'.format(name)))
            else:
                print(pad_message('Thread {} ended nicely'.format(name)))
                

    if signum == signal.SIGINT and _previous_sigint not in [None, signal.SIG_IGN, signal.SIG_DFL]:
        _previous_sigint(signum, frame)
    if signum == signal.SIGTERM and _previous_sigterm not in [None, signal.SIG_IGN, signal.SIG_DFL]:
        _previous_sigterm(signum, frame)
    # if signum == signal.SIGKILL and _previous_sigkill not in [None, signal.SIG_IGN, signal.SIG_DFL]:
        # _previous_sigkill(signum, frame)
        
    # force terminate
    os._exit(1) 
        
signal.signal(signal.SIGINT, _kill_threads_nicely)
signal.signal(signal.SIGTERM, _kill_threads_nicely)

def bb_endpoint_to_full_url(endpoint):
    return bitbucket_api_url + endpoint

def gh_endpoint_to_full_url(endpoint):
    return github_api_url + endpoint

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
    retry_count = 0
    response = None
    while retry:
        try:
            response = requests.get(endpoint, params=orig_params, auth=auth)
            if response.status_code == 429:
                retry_count += 1
                if retry_count%5 == 4:
                    mins_wait = retry_count//5 + 1
                    print(pad_message('({}) BitBucket API limit likely exceeded. Will retry in {} mins...'.format(auth[0], mins_wait)))
                    # TODO: make this a sleep(1) in a loop that checks elapsed time, so that PC hibernate negates the sleep
                    time.sleep(60*mins_wait)
                else:
                    time.sleep(5)
                continue
            retry = False
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError):
            retry_count += 1
            if retry_count%5 == 4:
                mins_wait = retry_count//5 + 1
                print(pad_message('({}) BitBucket API limit likely exceeded. Will retry in {} mins...'.format(auth[0], mins_wait)))
                # TODO: make this a sleep(1) in a loop that checks elapsed time, so that PC hibernate negates the sleep
                time.sleep(60*mins_wait)
            else:
                time.sleep(5)
            continue
        except BaseException:
            # retry = False
            raise

    if ABORT_EVENT.is_set():
        raise RuntimeError('Raising exception so that the thread ends sooner')

    return response

def bbapi_json(endpoint, auth, params=None):
    response = bb_query_api(endpoint, auth, params)
    try:
        json_response = response.json()
    except BaseException:
        json_response = None

    return response.status_code, json_response

def gh_query_api(endpoint, auth, params=None, data=None, headers=None):
    if not endpoint.startswith('https://'):
        endpoint = gh_endpoint_to_full_url(endpoint)
    endpoint, orig_params = full_url_to_query(endpoint)
    if params is not None:
        orig_params.update(params)
    # Catch the API limit
    retry = True
    response = None
    while retry:
        try:
            response = requests.get(endpoint, params=orig_params, auth=auth, data=data, headers=headers)
            retry = False
        except requests.exceptions.SSLError:
            print('API limit likely exceeded. Will retry in 5 mins...')
            time.sleep(60*5)
        except BaseException:
            # retry = False
            raise
    return response

def ghapi_json(endpoint, auth, params=None, data=None, headers=None):
    response = gh_query_api(endpoint, auth, params=params, data=data, headers=headers)
    try:
        json_response = response.json()
    except BaseException:
        json_response = None

    return response.status_code, json_response


def flatten_comments(hierarchy, comments, reordered_comments, depth=0):
    for h in hierarchy.values():
        c = comments[h['index']]
        # Add a depth counter so we know how far to indent, epecially if it's breaking nested commenst across pages
        if "parent" in c:
            c['parent']['depth'] = depth
        reordered_comments.append(c)
        flatten_comments(h['children'], comments, reordered_comments, depth=depth+1)
    return reordered_comments


def get_all_pages(data_directory, first_filepath):
    files = []
    while first_filepath is not None:
        with open(os.path.join(data_directory, *first_filepath.split('/')), 'r') as f:
            data = json.load(f)
            files.append(first_filepath)
            first_filepath = data.get('next')
    return files

import keyring
KEYRING_SERVICES = {
    'bitbucket': 'bitbucket-to-github-exporter/bitbucket',
    'github': 'bitbucket-to-github-exporter/github',
}
SERVICE_CHECKS = {
    'bitbucket': lambda credentials: bbapi_json('user', credentials),
    'github': lambda credentials: ghapi_json('user', credentials),
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
            'backup_forks': True,
            'generate_static_issue_pages': True,
            'generate_static_pull_request_pages': True,
            'generate_static_commit_comments_pages': True,

            'bitbucket_additional_users': [],

            'fork_search_complete': False,
            'bitbucket_api_download_complete': False,
            # 'bitbucket_api_URL_replace_complete': False,
            'bitbucket_api_download_complete_list': [],
            'bitbucket_hg_download_complete': False,

            'import_to_github': True,
            'master_github_username': '',
            'github_owner': '',
            'github_user_mapping_path': '',
            'bb_gh_user_mapping': {},
            'github_import_issues': True,
            'github_publish_pages': True,
            'github_pages_repo_name': '',
            'github_pages_url_type': 0,
            'github_pages_custom_url': '',
            'github_rewrite_additional_URLs': False,
            'github_URL_rewrite_file_path': '',
            'github_import_forks': True,
            'github_import_forks_to': None,
            'github_existing_repositories': {},
            'hg_to_git_tool': 'github',
            'local_git_repos_pushed': [],

            # 'github_import_complete': False,
            'github_git_download_complete': False,
            'github_issue_import_complete': False,

            'reorder_comments_complete': False,
            'hash_link_complete': False,
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
                project_name = q.select("Select a project to load?", choices=sorted(os.listdir(location))).ask()
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

        # questions about import to GitHub
        self.__get_github_import_options()

        self.__confirm_project_settings()

    def __confirm_project_settings(self, load=False):
        # confirm settings before beginning
        while not self.__print_project_settings():
            choices = {
                "Change primary BitBucket credentials":0, 
                "Add/remove additional BitBucket credentials":1, 
                "Change BitBucket repositories to export":2,
                "Change export settings":3,
                "Change primary GitHub credentials":4,
                "Change GitHub import settings":5,
            }
            if load:
                choices["Load different project"] = 6
            response = q.select("What would you like to change?", choices=choices.keys()).ask()
            if choices[response] == 0:
                self.__get_master_bitbucket_credentials(force_new_password=True)
            elif choices[response] == 1:
                self.__get_additional_bitbucket_credentials()
            elif choices[response] == 2:
                while not self.__get_bitbucket_repositories():
                    pass
            elif choices[response] == 3:
                self.__get_backup_options()
            elif choices[response] == 4:
                self.__get_master_github_credentials(force_new_password=True)
            elif choices[response] == 5:
                self.__get_github_import_options()
            elif choices[response] == 6:
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
            
            faq_read = q.confirm("I have read the entire readme at https://github.com/philipstarkey/bitbucket-hg-exporter (including the FAQ) and understood the caveats listed. I understand that I use this tool at my own risk. Answer 'Y' for agree or 'N' to terminate the program:", default=False).ask()
            if not faq_read:
                sys.exit(1)

            # load the user mapping
            if self.__settings['github_user_mapping_path']:
                while True:
                    try:
                        with open(self.__settings['github_user_mapping_path'], 'r') as f:
                            data = json.load(f)
                            if not isinstance(data, dict):
                                raise RuntimeError('')
                            self.__settings['bb_gh_user_mapping'] = data
                            break
                    except BaseException:
                        try_again = q.confirm('Could not load bitbucket to github user mapping file. Check it is a JSON dictionary containing bitbucket:github user key pairs. Would you like to try loading it again?').ask()
                        if not try_again:
                            break

            owner = self.__settings['bitbucket_repo_owner']
            auth = (self.__settings['master_bitbucket_username'], self.__get_password('bitbucket', self.__settings['master_bitbucket_username']))

            # this is a new setting, so people upgrading need to have it set to the prior behaviour
            if self.__settings['github_import_forks_to'] is None:
                self.__settings['github_import_forks_to'] = self.__settings['github_owner']
            self.__save_project_settings()

            all_repo_names = [repository['full_name'] for repository in self.__settings['bb_repositories_to_export']]
            initial_num_repos = len(all_repo_names)
            def recursively_process_repositories(repository):
                if 'links' not in repository or 'forks' not in repository['links'] or 'href' not in repository['links']['forks']:
                    return
                status, json_response = bbapi_json(repository['links']['forks']['href'], auth, {'pagelen':100})
                more = True
                while more:
                    if status == 200 and json_response is not None:
                        # process repositories (don't add duplicates)
                        for r in json_response['values']:
                            if r['full_name'] not in all_repo_names:
                                r['is_fork'] = True
                                self.__settings['bb_repositories_to_export'].append(r)
                                all_repo_names.append(r['full_name'])
                                print('Finding all forks of {}'.format(r['full_name']))
                                recursively_process_repositories(r)
                        if 'next' in json_response:
                            status, json_response = bbapi_json(json_response['next'], auth, {'pagelen':100})
                        else:
                            more = False
                    else:
                        print('Failed to query BitBucket API when determining forks for {}.'.format(repository['full_name']))
                        sys.exit(1)
            
            if self.__settings['backup_forks']:
                if self.__settings['fork_search_complete']:
                    search = q.confirm('A previous run of this script determined the list of all repository forks. Would you like to search for any new forks since the last run?', default=False).ask()
                else:
                    search = True

                if search:
                    for repository in self.__settings['bb_repositories_to_export']:
                        # recursively get list of all forks
                        print('Finding all forks of {}'.format(repository['full_name']))
                        recursively_process_repositories(repository)
                    self.__settings['fork_search_complete'] = True
            else:
                # remove forks
                self.__settings['bb_repositories_to_export'] = [repository for repository in self.__settings['bb_repositories_to_export'] if 'is_fork' not in repository or not repository['is_fork']]
                self.__settings['fork_search_complete'] = False
                # TODO: Should we clean up files left over from fork backup?
            self.__save_project_settings()

            # if we've added new forks, then we need to download them
            if len(self.__settings['bb_repositories_to_export']) > initial_num_repos:
                self.__settings['bitbucket_api_download_complete'] = False
                # self.__settings['bitbucket_api_URL_replace_complete'] = False

            if not self.__settings['bitbucket_api_download_complete']:
                import colorama
                colorama.init()
                auth_list = [auth] + [(user, self.__get_password('bitbucket', user)) for user in self.__settings['bitbucket_additional_users']]
                message_queue = queue.Queue()
                subset = [[] for _ in auth_list]
                threads = {}
                latest_messages = ['' for _ in auth_list]
                needs_processing = [repo for repo in self.__settings['bb_repositories_to_export'] if repo['full_name'] not in self.__settings['bitbucket_api_download_complete_list']]
                for i, repository in enumerate(needs_processing):
                    subset[i%len(auth_list)].append(repository['full_name'])

                def thread_fn(i, message_queue, credentials):
                    exporter = BitBucketExport(owner, credentials, copy.deepcopy(self.__settings), lambda cmd, message, i=i, q=message_queue:message_queue.put((i,cmd,message)), subset=subset[i])
                    exporter.backup_api()
                    message_queue.put((i, 'finished', ''))

                for i, credentials in enumerate(auth_list):
                    t = threading.Thread(target=thread_fn, args=(i, message_queue, credentials))
                    t.daemon = True
                    _REGISTERED_THREADS["Thread {} ({})".format(i, credentials[0])] = t
                    t.start()
                    threads[i] = t

                last_update_time = time.time()-1
                overwrite_last_lines = True
                while threads:
                    force = False
                    try:
                        i, cmd, message = message_queue.get(timeout=0.25)
                    except queue.Empty:
                        pass
                    else:
                        if cmd == 'update':
                            if len(message) == 3:
                                force = message[2]

                            if message[1] == '\n':
                                print(pad_message("Thread {} ({}): {}".format(i, auth_list[i][0], message[0])))
                                # overwrite_last_lines = False
                                force = True
                            else:
                                latest_messages[i] = message[0]
                        elif cmd == 'complete':
                            self.__settings['bitbucket_api_download_complete_list'].append(message)
                            self.__save_project_settings()
                        elif cmd == 'finished':
                            del threads[i]
                            force = True

                    if time.time() - last_update_time > 0.25 or force:
                        last_update_time = time.time()
                        pm = '\n'.join([pad_message("Thread {} ({}): {}".format(i, auth_list[i][0], m)) for i,m in enumerate(latest_messages)])
                        end = '\n'
                        if overwrite_last_lines and threads:
                            # calculate number of links to move up
                            # lines = len(auth_list)
                            lines = (len(pm)+1)//shutil.get_terminal_size()[0]
                            pm += '\x1b[%d;%dH'%(shutil.get_terminal_size()[1]-lines+1, 1) #+ pm
                            end = ''
                        print(pm, end=end)
                        overwrite_last_lines = True

                self.__settings['bitbucket_api_download_complete'] = True
                self.__save_project_settings()
                colorama.deinit()

            # clone the Hg repos (including forks if specified)            
            do_hg_pull = True
            if self.__settings['bitbucket_hg_download_complete']:
                do_hg_pull = q.confirm("hg repositories were pulled during a previous run of this script. Do you want to update them?", default=False).ask()

            logs = {}
            for repository in self.__settings['bb_repositories_to_export']:
                print('Processing mercurial repository {}'.format(repository['full_name']))
                # TODO: use password from mercurial_keyring (which I think means saving an additional keyring entry with
                # name and username as <username>@@<repo_url>)
                clone_dest = os.path.join(self.__settings['project_path'], 'hg-repos', *repository['full_name'].split('/'))
                clone_url = None
                for clone_link in repository['links']['clone']:
                    if clone_link['name'] == 'https':
                        clone_url = clone_link['href']
                        break
                if clone_url is None:
                    print('Failed to determine clone URL for BitBucket repository {}'.format(repository['full_name']))
                    sys.exit(1)
                clone_dests = [(clone_dest, clone_url)]

                # add path to wiki repository if it has one
                if repository['has_wiki']:
                    clone_dests.append((clone_dest+'-wiki', clone_url+'/wiki'))

                for clone_dest, clone_url in clone_dests:
                    if not os.path.exists(os.path.join(clone_dest, '.hg', 'hgrc')):
                        p=subprocess.Popen(['hg', 'clone', clone_url, clone_dest])
                        p.communicate()
                        if p.returncode:
                            print('Failed to hg clone {}'.format(clone_url))
                            sys.exit(1)
                    elif do_hg_pull:
                        p=subprocess.Popen(['hg', 'pull', '-R', clone_dest])
                        p.communicate()
                        if p.returncode:
                            print('Failed to hg update (pull) from {}'.format(clone_url))
                            sys.exit(1)

                # Generate mapping for rewriting changesets and other items
                logs[repository['full_name']] = {'hg': hg2git.get_hg_log(clone_dests[0][0])}

            self.__settings['bitbucket_hg_download_complete'] = True
            self.__save_project_settings()

            github_auth = ('', '') # empty values. Will be filled in if we are actually using GitHub (see a few lines below)
            github_headers = {"Accept": "application/vnd.github.barred-rock-preview"}

            # If needed, import all repositories to GitHub
            if self.__settings['import_to_github']:
                github_auth = (self.__settings['master_github_username'], self.__get_password('github', self.__settings['master_github_username']))
            
                if self.__settings['hg_to_git_tool'] == 'github':
                    for repository in self.__settings['bb_repositories_to_export']:
                        # skip forks if we are not importing them to github
                        if not self.__settings['github_import_forks']:
                            if 'is_fork' in repository and repository['is_fork']:
                                continue

                        # update status if we are in an error condition as this determines whether we should try again and we need to make sure we are not working from stale data
                        if repository['full_name'] in self.__settings['github_existing_repositories'] and self.__settings['github_existing_repositories'][repository['full_name']]['import_started'] and 'import_status' in self.__settings['github_existing_repositories'][repository['full_name']] and self.__settings['github_existing_repositories'][repository['full_name']]['import_status']['status'] == 'error':
                            import_status_check = requests.get(self.__settings['github_existing_repositories'][repository['full_name']]['import_url'], auth=github_auth, headers=github_headers)
                            if import_status_check.status_code == 200:
                                self.__settings['github_existing_repositories'][repository['full_name']]['import_status'] = import_status_check.json()
                            else:
                                pass
                                # handle this nicely
                        main_condition = repository['full_name'] not in self.__settings['github_existing_repositories'] or not self.__settings['github_existing_repositories'][repository['full_name']]['import_started']
                        error_condition = False
                        if not main_condition:
                            error_condition = ('import_status' in self.__settings['github_existing_repositories'][repository['full_name']] and self.__settings['github_existing_repositories'][repository['full_name']]['import_status']['status'] == 'error' and self.__settings['github_existing_repositories'][repository['full_name']]['import_status'].get("message", '') != "The imported repository is empty.")
                        if main_condition or error_condition:
                            clone_url = None
                            for clone_link in repository['links']['clone']:
                                if clone_link['name'] == 'https':
                                    clone_url = clone_link['href']
                                    break
                            if clone_url is None:
                                print('Failed to determine clone URL for BitBucket repository {}'.format(repository['full_name']))
                                sys.exit(1)

                            github_slug = self.create_github_slug(repository)

                            # Need to create the repository first! This should allow us to make it private! Yay!
                            # check if repository already exists
                            owner = self.__settings['github_import_forks_to'] if 'is_fork' in repository and repository['is_fork'] else self.__settings['github_owner']
                            if repository['full_name'] not in self.__settings['github_existing_repositories']:
                                response = self.create_or_get_github_repository(owner, github_slug, repository, github_auth)                            

                                self.__settings['github_existing_repositories'][repository['full_name']] = {
                                    'name': '{owner}/{repo_name}'.format(owner=owner, repo_name=github_slug),
                                    'repository': response,
                                    'import_started': False,
                                    'import_completed': False
                                }
                                self.__save_project_settings()

                            # cancel any error requests
                            if error_condition:
                                print('Cancelling import for repository {owner}/{repo_name}) as it was in an error state. We will re-request the import shortly.'.format(owner=owner, repo_name=github_slug))
                                response = requests.delete('https://api.github.com/repos/{owner}/{repo_name}/import'.format(owner=owner, repo_name=github_slug), auth=github_auth, headers=github_headers)
                                if response.status_code != 204:
                                    print('WARNING: Failed to cancel import with error state (repository: {owner}/{repo_name}). We suggest visiting github.com/{owner}/{repo_name} and attempting to restart the import from there.'.format(owner=owner, repo_name=github_slug))
                                    continue
                            
                            # generate import request to GitHub
                            # TODO: Make this work for private repositories
                            #       Need to confirm with user that they are happy for their BitBucket credentials to be given to GitHub
                            params = {
                                "vcs": "mercurial",
                                "vcs_url": clone_url,
                                # "vcs_username": auth[0],
                                # "vcs_password": auth[1]
                            }
                            print('Requesting source import for repository {}/{}'.format(owner, github_slug))
                            response = requests.put('https://api.github.com/repos/{owner}/{repo_name}/import'.format(owner=owner, repo_name=github_slug), auth=github_auth, headers=github_headers, json=params)
                            if response.status_code != 201:
                                print('Failed to import BitBucket repository {} to GitHub. Response code was: {}'.format(repository['full_name'], response.status_code))
                                sys.exit(1)
                            self.__settings['github_existing_repositories'][repository['full_name']].update({
                                'initial_import_response': response.json(),
                                'import_url': 'https://api.github.com/repos/{owner}/{repo_name}/import'.format(owner=owner, repo_name=github_slug),
                                'import_started': True,
                            })
                            self.__save_project_settings()
                            # enable LFS
                            response = requests.patch('https://api.github.com/repos/{owner}/{repo_name}/import/lfs'.format(owner=owner, repo_name=github_slug), auth=github_auth, headers=github_headers, json={"use_lfs": "opt_in"})

                    # wait for all imports to complete
                    all_finished = False
                    while not all_finished:
                        all_finished = True
                        for bitbucket_name, github_data in self.__settings['github_existing_repositories'].items():
                            if 'initial_import_response' not in github_data:
                                # A hack to handle repos that already existed and were not imported
                                # (we use this URL later when cloning the GitHub repo)
                                github_data['import_status'] = {}
                                github_data['import_status']['repository_url'] = 'https://api.github.com/repos/'+github_data['name']
                                # Skip checking imprt status for repos we didn't import ourselves
                                continue
                            if 'import_status' not in github_data or github_data['import_status']['status'] != 'complete':
                                # get the current status
                                response = requests.get(github_data['import_url'], auth=github_auth, headers=github_headers)
                                if response.status_code != 200:
                                    all_finished = False
                                    print('Failed to check status of import to {}. Will try again next loop.'.format(github_data['name']))
                                    continue
                                github_data['import_status'] = response.json()
                                empty_repo = (github_data['import_status']['status'] == 'error' and github_data['import_status'].get("message", '') == "The imported repository is empty.")
                                if github_data['import_status']['status'] != 'complete' and not empty_repo:
                                    print('Waiting on {} to complete. Current status is: {}'.format(github_data['name'],github_data['import_status']['status_text']))
                                    all_finished = False
                                else:
                                    github_data['import_completed'] = True
                                self.__save_project_settings()
                        if not all_finished:
                            print('sleeping for 30 seconds...')
                            time.sleep(30)
                elif self.__settings['hg_to_git_tool'] == 'local':
                    # TODO: Write this
                    repo_mapping = {}
                    for repository in self.__settings['bb_repositories_to_export']:
                        # skip forks if we are not importing them to github
                        if not self.__settings['github_import_forks']:
                            if 'is_fork' in repository and repository['is_fork']:
                                continue

                        github_slug = self.create_github_slug(repository)
                        owner = self.__settings['github_import_forks_to'] if 'is_fork' in repository and repository['is_fork'] else self.__settings['github_owner']
                        response = self.create_or_get_github_repository(owner, github_slug, repository, github_auth)                            

                        github_repo_name = '{owner}/{repo_name}'.format(owner=owner, repo_name=github_slug)
                        self.__settings['github_existing_repositories'][repository['full_name']] = {
                            'name': github_repo_name,
                            'repository': response,
                            'import_started': False,
                            'import_completed': False,
                            'import_status': {
                                'repository_url': 'https://api.github.com/repos/'+github_repo_name
                            }
                        }
                        self.__save_project_settings()

                        hg_clone_dest = os.path.join(self.__settings['project_path'], 'hg-repos', *repository['full_name'].split('/'))
                        git_clone_dest = os.path.join(self.__settings['project_path'], 'git-repos', *github_repo_name.split('/'))
                        repo_mapping[hg_clone_dest] = git_clone_dest

                    # TODO: provide option to skip the mapping file write and user choice if it has been done before

                    # write out the mapping file
                    with open(os.path.join(self.__settings['project_path'], 'repo_mapping.json'), 'w') as f:
                        json.dump(repo_mapping, f, indent=4)

                    print('A mapping between local mercurial repository and expected git repository locations has been written to "repo_mapping.json" in the project directory.')
                    print('Please ensure that your local tool creates git repositories in the specified locations before continuing.')
                    # Pause and continue on user command
                    choices = {
                        "Continue (my local tool has produced git repositories in the appropriate location)":0, 
                        "Exit (you can resume this tool later by loading the project and starting the export again)":1,
                    }
                    response = q.select("Select an option below:", choices=choices.keys()).ask()
                    if choices[response] != 0:
                        sys.exit(0)

                    # push repositories to git
                    # git remote add origin https://github.com/<blah>/<blah>
                    # git push origin --all # All branches
                    # git push origin --tags # All tags
                    # git push origin refs/notes/* # All notes
                    for repository in self.__settings['bb_repositories_to_export']:
                        # skip forks if we are not importing them to github
                        if not self.__settings['github_import_forks']:
                            if 'is_fork' in repository and repository['is_fork']:
                                continue
                        github_repo_name = self.__settings['github_existing_repositories'][repository['full_name']]['name']
                        clone_dest = os.path.join(self.__settings['project_path'], 'git-repos', *github_repo_name.split('/'))
                        if github_repo_name not in self.__settings['local_git_repos_pushed']:
                            clone_url = 'https://github.com/{name}'.format(name=github_repo_name)
                            self.call_git_subprocess('remote', 'add', 'origin', clone_url, cwd=clone_dest, error_message='Failed to add remote for git repository at {path}. Maybe it\'s already been added'.format(path=clone_dest), exit=False)
                            self.call_git_subprocess('push', 'origin', '--all', cwd=clone_dest,  error_message='Failed to push {path} to remote repository'.format(path=clone_dest), retry=True)
                            self.call_git_subprocess('push', 'origin', '--tags', cwd=clone_dest,  error_message='Failed to push tags in {path} to remote repository'.format(path=clone_dest), retry=True)
                            self.call_git_subprocess('push', 'origin', 'refs/notes/*', cwd=clone_dest,  error_message='Failed to push notes in {path} to remote repository'.format(path=clone_dest), retry=True)
                            self.__settings['local_git_repos_pushed'].append(github_repo_name)
                            self.__save_project_settings()

                        # Get any known hg-> git hash mapping from the git notes of the converted repository
                        logs[repository['full_name']]['git_hg_hashes'] = hg2git.get_hg_hashes_from_git(clone_dest)
                else:
                    print('Unknown conversion hg->git conversion tool specified.')
                    sys.exit(1)
                # TODO: send user mappings

                do_git_pull = True
                if self.__settings['github_git_download_complete']:
                    do_git_pull = q.confirm("git repositories were pulled during a previous run of this script. Do you want to update them?", default=False).ask()

                # clone the Github repos if needed
                for repository in self.__settings['bb_repositories_to_export']:
                    # skip forks if we are not importing them to github
                    if not self.__settings['github_import_forks']:
                        if 'is_fork' in repository and repository['is_fork']:
                            continue

                    # get the github repository information
                    github_data = self.__settings['github_existing_repositories'][repository['full_name']]
                    response = requests.get(github_data['import_status']['repository_url'], auth=github_auth, headers=github_headers)
                    if response.status_code != 200:
                        print('Failed to get GitHub repository information for {}'.format(github_data['name']))
                        sys.exit(1)
                    github_data['repository'] = response.json()
                    self.__save_project_settings()

                    print('Processing git repository {}'.format(github_data['name']))

                    # TODO: use password from github keyring?
                    clone_dest = os.path.join(self.__settings['project_path'], 'git-repos', *github_data['name'].split('/'))
                    clone_url =  github_data['repository']['clone_url']
                    if not os.path.exists(os.path.join(clone_dest, '.git', 'config')):
                        self.call_git_subprocess('clone', clone_url, clone_dest, error_message='Failed to git clone {}'.format(clone_url))
                        # p=subprocess.Popen(['git', 'clone', clone_url, clone_dest])
                        # p.communicate()
                        # if p.returncode:
                        #     print('Failed to git clone {}'.format(clone_url))
                        #     sys.exit(1)
                        
                        # git fetch origin refs/notes/*:refs/notes/*
                        self.call_git_subprocess('fetch', 'origin', 'refs/notes/*:refs/notes/*', cwd=clone_dest,  error_message='Failed to pull notes from remote repository to {path}'.format(path=clone_dest), exit=False)

                    elif do_git_pull:
                        if not self.call_git_subprocess('pull', clone_url, cwd=clone_dest, error_message='Failed to git update (pull) from {}'.format(clone_url), exit=False):
                            print('This is probably because the repository is empty? We\'ll try and continue...')
                        # p=subprocess.Popen(['git', 'pull', clone_url], cwd=clone_dest)
                        # p.communicate()
                        # if p.returncode:
                        #     print('Failed to git update (pull) from {}'.format(clone_url))
                        #     if os.path.exists(os.path.join(clone_dest, '.git', 'index')):
                        #         sys.exit(1)
                        #     else:
                        #         print('This is probably because the repository is empty? We\'ll try and continue...')
                        
                        # git fetch origin refs/notes/*:refs/notes/*
                        self.call_git_subprocess('fetch', 'origin', 'refs/notes/*:refs/notes/*', cwd=clone_dest,  error_message='Failed to pull notes from remote repository to {path}'.format(path=clone_dest), exit=False)

                    # Generate mapping for rewriting changesets and other items
                    logs[repository['full_name']]['git'] = hg2git.get_git_log(clone_dest)

                self.__settings['github_git_download_complete'] = True
                self.__save_project_settings()

            mapping = {}
            if self.__settings['import_to_github']:
                # Generate the git repo logs
                for repository in self.__settings['bb_repositories_to_export']:
                    # skip forks if we are not importing them to github
                    if not self.__settings['github_import_forks']:
                        if 'is_fork' in repository and repository['is_fork']:
                            continue

                    # create the mapping
                    owner = self.__settings['github_import_forks_to'] if 'is_fork' in repository and repository['is_fork'] else self.__settings['github_owner']
                    # archive_url = 'https://{owner}.github.io/{repo}/'.format(owner=owner, repo=self.__settings['github_pages_repo_name'])
                    archive_url = self.get_github_pages_url()
                    archive_url += '/#!/'+repository['full_name']
                    known_hg_git_mapping = {}
                    if 'git_hg_hashes' in logs[repository['full_name']]:
                        known_hg_git_mapping = logs[repository['full_name']]['git_hg_hashes']
                    mapping[repository['full_name']] = hg2git.BbToGh(logs[repository['full_name']]['hg'], logs[repository['full_name']]['git'], repository['links']['html']['href'], self.__settings['github_existing_repositories'][repository['full_name']]['repository']['html_url'], self.__settings['bb_gh_user_mapping'], archive_url=archive_url, known_hg_git_mapping=known_hg_git_mapping)

            

            # rewrite repository URLS (especiallly inter-repo issues and PRs and forks if appropriate) and changesets    
            # (also use the additional URLs to rewrite JSON file)
            #
            # Things we need to handle that probably aren't at the moment
            #   * markup-less commit references
            #   * URLS in issues such as links to forks, issues, and changesets (which currently always have a api.bitbucket.org link that is malformed...)
            #   * decide if we want to rewrite the commit messages in the git repository, which means we need to:
            #       a) Do it in the order of earlist to latest (since commit hashes will change when we do this)
            #       b) update the acquired git log with the new hash
            #       c) Make sure that all the authors are mapped appropriately since force pushing to the github repo will result in you not being able to map any more users.
            
            # Note: This is now done as part of the call to exporter.backup_api() in order to not need to hold the entire structure of the 
            #       BitBucket API (for every repo) in RAM until all repos/forks are downloaded.
            #
            # rewrite URLS to reference the downloaded ones
            # if not self.__settings['bitbucket_api_URL_replace_complete']:
            #     exporter.make_urls_relative(mapping=mapping)
            #     self.__settings['bitbucket_api_URL_replace_complete'] = True
            #     self.__save_project_settings()

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

            # write out a list of downloaded repos and a link to their top level JSON file and other important JSON files
            top_level_repo_data = {}
            with open(os.path.join(self.__settings['project_path'], 'gh-pages', 'repos.json'), 'w') as f:
                data = {}
                for repository in self.__settings['bb_repositories_to_export']:
                    data[repository['full_name']] = {
                        'project_file': 'data/repositories/{}.json'.format(repository['full_name']),
                        'project_path': 'data/repositories/{}/'.format(repository['full_name']),
                        'is_fork': 'is_fork' in repository and repository['is_fork'],
                    }

                    # save github repo location (so we can link to changesets, files, etc)
                    if repository['full_name'] in self.__settings['github_existing_repositories']:
                        data[repository['full_name']]['github_repo'] = self.__settings['github_existing_repositories'][repository['full_name']]['repository']['html_url']

                    # load the top level JSON file for each project as we will use it more than once
                    data_path = os.path.join(self.__settings['project_path'], 'gh-pages', 'data', 'repositories', *repository['full_name'].split('/'))
                    pull_request_path = None
                    with open(data_path + '.json', 'r') as g:
                        top_level_repo_data[repository['full_name']] = json.load(g)

                    # if "links" in top_level_repo_data[repository['full_name']]:
                    #     # write out links to issue files, pull requests, etc.
                    #     for link_type in ['issues', 'pullrequests']:
                    #         link_filepaths = get_all_pages(
                    #             os.path.join(self.__settings['project_path'], 'gh-pages'),
                    #             top_level_repo_data[repository['full_name']]['links'].get(link_type, {}).get('href')
                    #         )
                    #         data[repository['full_name']]['{}_files'.format(link_type)] = dict(enumerate(link_filepaths, 1))
                json.dump(data, f, indent=4)

            with open(os.path.join(self.__settings['project_path'], 'gh-pages', 'user_mapping.json'), 'w') as f:
                json.dump(self.__settings['bb_gh_user_mapping'], f, indent=4)

            # TODO: write out a site pages list for search indexing

            # reprocess:
            #   * PR comments so they are in a useful order
            print('Reordering comments...')
            
            if not self.__settings['reorder_comments_complete']:
                for repository in self.__settings['bb_repositories_to_export']:
                    # open repo.json file, find location of pull requests list
                    pull_request_path = None
                    repo_data = top_level_repo_data[repository['full_name']]
                    if "links" in repo_data and "pullrequests" in repo_data['links'] and 'href' in repo_data['links']['pullrequests']:
                        pull_request_path = os.path.join(self.__settings['project_path'], 'gh-pages', *repo_data['links']['pullrequests']['href'].split('/'))

                    # open that file, iterate over each pull requests, and find links to comments
                    comment_paths = []
                    while pull_request_path is not None:
                        with open(pull_request_path, 'r') as f:
                            pull_requests_data = json.load(f)
                            for pull_request in pull_requests_data['values']:
                                if 'links' in pull_request and 'comments' in pull_request['links'] and 'href' in pull_request['links']['comments']:
                                    comment_paths.append(os.path.join(self.__settings['project_path'], 'gh-pages', *pull_request['links']['comments']['href'].split('/')))
                            if "next" in pull_requests_data:
                                pull_request_path = os.path.join(self.__settings['project_path'], 'gh-pages',  *pull_requests_data['next'].split('/'))
                            else:
                                pull_request_path = None

                    # find location of commit list
                    if "links" in repo_data and "commits" in repo_data['links'] and 'href' in repo_data['links']['commits']:
                        pull_request_path = os.path.join(self.__settings['project_path'], 'gh-pages', *repo_data['links']['commits']['href'].split('/'))

                    # open that file, iterate over each commit, and find links to comments
                    # TODO: rename variables
                    while pull_request_path is not None:
                        with open(pull_request_path, 'r') as f:
                            pull_requests_data = json.load(f)
                            for pull_request in pull_requests_data['values']:
                                if 'links' in pull_request and 'comments' in pull_request['links'] and 'href' in pull_request['links']['comments']:
                                    comment_paths.append(os.path.join(self.__settings['project_path'], 'gh-pages', *pull_request['links']['comments']['href'].split('/')))
                            if "next" in pull_requests_data:
                                pull_request_path = os.path.join(self.__settings['project_path'], 'gh-pages',  *pull_requests_data['next'].split('/'))
                            else:
                                pull_request_path = None

                    # Note this code now handles both pull requests and commit comments (despite the variable names)
                    for pull_request_file in comment_paths:
                        comment_files = [pull_request_file]
                        comments = []
                        # Load all comments into RAM, then recursively iterate finding all the ones that have no parent, then all children of the top level, then children of that level, etc. etc. until all comments are placed into a hierarchy. 
                        while pull_request_file:
                            with open(pull_request_file, 'r') as f:
                                comment_data = json.load(f)
                                if 'values' in comment_data:
                                    comments.extend(comment_data['values'])

                                if 'next' in comment_data:
                                    pull_request_file = os.path.join(self.__settings['project_path'], 'gh-pages',  *comment_data['next'].split('/'))
                                    comment_files.append(pull_request_file)
                                else:
                                    pull_request_file = None

                        done_idxs = []
                        all_idxs = [c['id'] for c in comments]
                        all_parent_idxs = {c['parent']['id']:i for i, c in enumerate(reversed(comments)) if 'parent' in c}
                        dummy_comments = 0
                        for parent_idx, insert_index in all_parent_idxs.items():
                            if parent_idx not in all_idxs:
                                # insert dummy comment for parent before the comment if it is missing
                                child_comment = comments[insert_index]
                                dummy_comment = {
                                    "links": {
                                        "self": {
                                            "href": child_comment['links']['self']['href'].replace(
                                                '/{}.json'.format(child_comment['id']), 
                                                '/{}.json'.format(parent_idx)
                                            )
                                        },
                                        "html": {
                                            "href": child_comment['links']['html']['href'].replace(
                                                '#comment-{}'.format(child_comment['id']), 
                                                '#comment-{}'.format(parent_idx)
                                            )
                                        }
                                    },
                                    "deleted": False,
                                    "pullrequest": copy.deepcopy(child_comment['pullrequest']),
                                    "content": {
                                        "raw": "This comment could not be backed up from BitBucket, so this dummy comment was inserted in it's place.",
                                        "markup": "markdown",
                                        "html": "<p>This comment could not be backed up from BitBucket, so this dummy comment was inserted in it's place.</p>",
                                        "type": "rendered"
                                    },
                                    "created_on": "1970-01-01T00:00:00.000000+00:00",
                                    "user": {
                                        "display_name": "Unknown User",
                                        "uuid": "{00000000-0000-0000-0000-000000000000}",
                                        "links": {
                                            "self": {
                                                "href": "https://api.bitbucket.org/2.0/users/%7B00000000-0000-0000-0000-000000000000%7D"
                                            },
                                            "html": {
                                                "href": "https://bitbucket.org/%7B00000000-0000-0000-0000-000000000000%7D/"
                                            },
                                            "avatar": {
                                                "href": "data/missing_avatar.png"
                                            }
                                        },
                                        "nickname": "unknownuser",
                                        "type": "user",
                                        "account_id": "000000:00000000-0000-0000-0000-000000000000"
                                    },
                                    "updated_on": "1970-01-01T00:00:00.000000+00:00",
                                    "type": "pullrequest_comment",
                                    "id": parent_idx
                                }
                                comments.insert(insert_index, dummy_comment)
                                print('Comment with id {} was missing (you should have seen an earlier error about this during the download stage). There is not much we can do about it if BitBucket refuses to give us the data, so we have inserted a dummy comment in place so that child comments at least show up correctly.'.format(parent_idx))
                                dummy_comments += 1

                        # create an additional file if inserting dummy comments pushed the number over the limit
                        if (len(comments)//100+1) > len(comment_files):
                            *new_file_path_prefixes, new_file_path_suffix = comment_files[-1].split('=')
                            page_num, extension = new_file_path_suffix.split('.')
                            page_num = int(page_num) + 1
                            new_file_path = '='.join(new_file_path_prefixes+[page_num]) + '.' + extension
                            # open old file and add "next" page
                            with open(comment_files[-1], 'r') as f:
                                prev_file_data = json.load(f)
                            prev_file_data['next'] = new_file_path
                            with open(comment_files[-1], 'w') as f:
                                json.dump(prev_file_data, f)
                            # open new file and write out structure
                            with open(new_file_path, 'w') as f:
                                new_file_data = {
                                    "previous": comment_files[-1],
                                    "pagelen": 100, 
                                    "size": prev_file_data['size'], 
                                    "page": page_num,
                                    "values": [] # no need to actually write out unordered data. It gets written later
                                }
                                json.dump(new_file_data, f)
                            # add newly created file to the list
                            comment_files.append(new_file_path)
                                

                        comment_flat = {}
                        comment_hierarchy = OrderedDict()
                        while len(done_idxs) < len(comments):
                            for i, comment in enumerate(comments):
                                if i in done_idxs:
                                    continue
                                found_parent = False
                                if "parent" not in comment:
                                    parent = comment_hierarchy
                                    found_parent = True
                                elif comment['parent']['id'] in comment_flat:
                                    parent = comment_flat[comment['parent']['id']]['children']
                                    found_parent = True

                                if found_parent:
                                    done_idxs.append(i)
                                    d = {
                                        'children': OrderedDict(),
                                        'index': i,
                                    }
                                    parent[comment['id']] = d
                                    comment_flat[comment['id']] = d
                        
                        # Then flatten, split into chunks
                        reordered_comments = flatten_comments(comment_hierarchy, comments, [])
                        for i, pull_request_file in enumerate(comment_files):
                            with open(pull_request_file, 'r') as f:
                                comment_data = json.load(f)
                                comment_data['values'] = reordered_comments[i*100:(i+1)*100]
                                if i==0 and len(reordered_comments) - dummy_comments != comment_data['size']:
                                    print('Warning: Something went wrong reordering the pull requests comments in file {}. The number of comments we are writing does not agree with how many there were before we reordered them. There were {} comments, now {} comments'.format(pull_request_file, comment_data['size'], len(reordered_comments)))
                                # update this in case we had to insert a dummy comment
                                comment_data['size'] = len(reordered_comments)
                            with open(pull_request_file, 'w') as f:
                                json.dump(comment_data, f)
                self.__settings['reorder_comments_complete'] = True
                self.__save_project_settings()
            print('done!')

            # Write out the mapping between mercurial and gitub hashes
            link_hashes = True
            if self.__settings['import_to_github'] and self.__settings['hash_link_complete']:
                link_hashes = q.confirm("mercurial and git hashes have already been linked (and tag info obtained) on a previous run. Do you want to refresh this data? (useful if you have updated the local hg/git repos or some commits did not have their hashes linked correctly in a previous run)", default=False).ask()

            if self.__settings['import_to_github'] and link_hashes:
                print('Linking git and mercurial hashes...')
                missing_git_commits = {}
                for repository in self.__settings['bb_repositories_to_export']:
                    # skip forks if we are not importing them to github
                    if not self.__settings['github_import_forks']:
                        if 'is_fork' in repository and repository['is_fork']:
                            continue

                    # load .hgtags file if it exists
                    hg_tags_path = os.path.join(self.__settings['project_path'], 'hg-repos', *repository['full_name'].split('/'), '.hgtags')
                    hg_tags = {}
                    if os.path.exists(hg_tags_path):
                        with open(hg_tags_path, 'r') as f:
                            for line in f:
                                parts = line.split(' ')
                                node_tags = hg_tags.get(parts[0], [])
                                node_tags.append(" ".join(parts[1:]))
                            
                    repo_api_path = os.path.join(self.__settings['project_path'], 'gh-pages', 'data', 'repositories', *repository['full_name'].split('/'))
                    for filename in os.listdir(os.path.join(repo_api_path, 'commit')):
                        if filename.endswith('.json'):
                            try:
                                with open(os.path.join(repo_api_path, 'commit', filename), 'r') as f:
                                    data = json.load(f)
                            except BaseException:
                                print(repo_api_path, filename)
                                raise
                                sys.exit(1)
                            # get git hash
                            try:
                                data['git_hash'] = mapping[repository['full_name']].hgnode_to_githash(data['hash'])
                            except BaseException:
                                print('Failed to get git hash for BitBucket {} (hash: {})'.format(repository['full_name'], data['hash']))
                                data['git_hash'] = None
                            # get tags
                            data['tags'] = hg_tags[data['hash']] if data['hash'] in hg_tags else None
                            # get branch(es) (note: hg log command calls this "branches" but I think there is only ever one branch name for a commit)
                            data['branches'] = 'default'
                            if data['hash'] in mapping[repository['full_name']].hg_branches and mapping[repository['full_name']].hg_branches[data['hash']]:
                                data['branches'] = mapping[repository['full_name']].hg_branches[data['hash']]
                            if data['git_hash'] is None:
                                if repository['full_name'] not in missing_git_commits:
                                    missing_git_commits[repository['full_name']] = []
                                missing_git_commits[repository['full_name']].append(data['hash'])
                                print('Warning: a matching commit for hg_hash:{hg_hash} was not found in the git repository but the BitBucket API for {repo} said that it exists.'.format(hg_hash=data['hash'], repo=repository['full_name']))
                            
                            with open(os.path.join(repo_api_path, 'commit', filename), 'w') as f:
                                # write out the data
                                json.dump(data, f)
                if missing_git_commits:
                    filepath = os.path.join(self.__settings['project_path'], 'missing_commits.json')
                    with open(filepath, 'w') as f:
                        json.dump(missing_git_commits, f)
                    print("WARNING: Some commits could not be matched between hg and git and may indicate some commits were not imported to GitHub correctly (for example, branches with multiple heads). Check carefully that you have not lost commit data in the migration to GitHub. A list of missing commits has been saved to {}.".format(filepath))
                self.__settings['hash_link_complete'] = True
                self.__save_project_settings()
                print('done!')

            # Upload the pages to GitHub
            if self.__settings['import_to_github'] and self.__settings['github_publish_pages']:
                print('Uploading the archive of BitBucket data to GitHub and activating GitHub pages')
                clone_dest = os.path.join(self.__settings['project_path'], 'gh-pages')
                clone_url = 'https://github.com/{owner}/{repo}'.format(owner=self.__settings['github_owner'], repo=self.__settings['github_pages_repo_name'])
                if not os.path.exists(os.path.join(clone_dest, '.git', 'index')):
                    # create repository
                    p=subprocess.Popen(['git', 'init'], cwd=clone_dest)
                    p.communicate()
                    if p.returncode:
                        print('Failed to run git init for gh-pages folder')
                        sys.exit(1)

                    # set remote
                    p=subprocess.Popen(['git', 'remote', 'add', 'origin', clone_url], cwd=clone_dest)
                    p.communicate()
                    if p.returncode:
                        print('Failed to run git init for gh-pages folder')
                        sys.exit(1)
                else:
                    # pull latest version
                    p=subprocess.Popen(['git', 'pull', clone_url], cwd=clone_dest)
                    p.communicate()
                    if p.returncode:
                        print('WARNING: Failed to git update (pull) from {}'.format(clone_url))

                # Stage all changes
                p=subprocess.Popen(['git', 'add', '.'], cwd=clone_dest)
                p.communicate()
                if p.returncode:
                    print('Failed to stage changes in gh-pages folder')
                    sys.exit(1)

                # commit all changes
                p=subprocess.Popen(['git', 'commit', '-m', "Auto commit by bitbucket_hg_exporter at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'))], cwd=clone_dest)
                p.communicate()
                if p.returncode:
                    print('Failed to commit changes in gh-pages folder, ignoring because this was probably because there was nothing to commit.')
                    # sys.exit(1)

                # Make GitHub repo if needed
                # the details need to be formatted as a BitBucket repository as that is what the method expects.
                gh_pages_repo_details = {
                    "description": "Archive of repository data from BitBucket",
                    "is_private": False,
                    "has_wiki": False,
                    "has_issues": False,
                    "has_projects": False,
                    'website': self.get_github_pages_url(),
                }
                response = self.create_or_get_github_repository(self.__settings['github_owner'], self.__settings['github_pages_repo_name'], gh_pages_repo_details, github_auth)

                # Push to GitHub
                p=subprocess.Popen(['git', 'push', 'origin', 'master'], cwd=clone_dest)
                p.communicate()
                if p.returncode:
                    print('Failed to push changes in gh-pages folder')
                    sys.exit(1)

                
                # Configure for github pages
                github_headers = {"Accept": 'application/vnd.github.switcheroo-preview+json'}
                pages_data = {
                    "source": {
                        "branch": "master",
                        "path": ""
                    }
                }
                response = requests.post(
                    'https://api.github.com/repos/{owner}/{repo}/pages'.format(owner=self.__settings['github_owner'], repo=self.__settings['github_pages_repo_name']),  
                    auth=github_auth, 
                    headers=github_headers,
                    json=pages_data
                )
                # Only error on response codes that are not success or "already enabled"
                if response.status_code != 201 and response.status_code != 409:
                    print('Failed to enable GitHub pages on {}/{} (for the BitBucket archive). Response code was: {}'.format(self.__settings['github_owner'], self.__settings['github_pages_repo_name'], response.status_code))
                    print(response.json())
                    sys.exit(1)

                # Update teh custom domain
                if self.__settings['github_pages_url_type'] == 1:
                    pages_data = {
                        "cname": self.__settings['github_pages_custom_url'],
                        "source": "master"
                    }
                else:
                    pages_data = {
                        "cname": None,
                        "source": "master"
                    }
                response = requests.put(
                    'https://api.github.com/repos/{owner}/{repo}/pages'.format(owner=self.__settings['github_owner'], repo=self.__settings['github_pages_repo_name']),  
                    auth=github_auth, 
                    json=pages_data
                )
                if response.status_code != 204:
                    print('Failed to update custom domain to "{}" on {}/{} (for the BitBucket archive). Response code was: {}'.format(self.__settings['github_pages_custom_url'], self.__settings['github_owner'], self.__settings['github_pages_repo_name'], response.status_code))
                    print(response.json())
                    sys.exit(1)
                print('done!')
            
            # Upload issues to GitHub if requested (using rewritten URLs/changesets)
            # URL to get last existing issue number: https://api.github.com/search/issues?q=repo:philipstarkey/qtutils+sort:author-date-desc&sort=created&order=desc
            if self.__settings['import_to_github'] and self.__settings['github_import_issues'] and not self.__settings['github_issue_import_complete']:
                print('Performing dry run of GitHub issue import')
                for repository in self.__settings['bb_repositories_to_export']: 
                    bb_repo = repository['full_name']
                    gh_repo = self.__settings['github_existing_repositories'][bb_repo]['repository']['full_name']
                    import_issues_to_github(bb_repo, gh_repo, github_auth, copy.deepcopy(self.__settings), mapping, dry_run=True)
                print('done! (you can see the results in the "temp/<owner>/<repo>" folder in the project directory)')

                do_import = q.confirm('Do you want to proceed with the import of issues to GitHub (this can only be attempted once)?', default=False).ask()

                if do_import:
                    for repository in self.__settings['bb_repositories_to_export']: 
                        bb_repo = repository['full_name']
                        gh_repo = self.__settings['github_existing_repositories'][bb_repo]['repository']['full_name']
                        print('Importing issues from BitBucket/{} to GitHub/{}'.format(bb_repo, gh_repo))
                        import_issues_to_github(bb_repo, gh_repo, github_auth, copy.deepcopy(self.__settings), mapping, dry_run=False)
                    print('done!')

                    self.__settings['github_issue_import_complete'] = True
                    self.__save_project_settings()


            

            # Import wikis

        elif choices[response] == 1:
            sys.exit(0)
        else:
            raise RuntimeError('Unknown option selected')

    def create_or_get_github_repository(self, owner, github_slug, bb_repository_details, github_auth=None):
        repository = bb_repository_details

        # query if the repository already exists
        status, response = ghapi_json('repos/{owner}/{repo}'.format(owner=owner, repo=github_slug), github_auth)
        # only create if it doesn't exist
        if status != 200 or (status == 200 and response['full_name'] != '{owner}/{repo}'.format(owner=owner, repo=github_slug)):
            # find out if owner is a user or org
            is_org = False
            status, response = ghapi_json('users/{owner}'.format(owner=owner), github_auth)
            if status == 200:
                if response['type'] != "User":
                    is_org = True

            repo_data = {
                "name": github_slug,
                "description": repository['description'].replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' '),
                "private": repository['is_private'],
                "has_wiki": repository['has_wiki'],
                "has_issues": repository['has_issues'],
                "has_projects": True if "has_projects" not in repository else repository['has_projects']
            }
            if repository['website']:
                repo_data['homepage'] = repository['website']
            print('Creating repository {}/{}'.format(owner, github_slug))
            if is_org:
                response = requests.post(
                    'https://api.github.com/orgs/{owner}/repos'.format(owner=owner),  
                    auth=github_auth, 
                    json=repo_data
                )
            else:
                response = requests.post(
                    'https://api.github.com/user/repos',  
                    auth=github_auth, 
                    json=repo_data
                )
            if response.status_code != 201:
                print('Failed to create empty repository {}/{} on GitHub. Response code was: {}'.format(owner, github_slug, response.status_code))
                print("Error response: ", response.text)
                sys.exit(1)
            response = response.json()

        return response

    def create_github_slug(self, bb_repository_details):
        if 'is_fork' in bb_repository_details and bb_repository_details['is_fork']:
            github_slug = bb_repository_details['full_name'].replace('/', '-')
            if 'parent' in bb_repository_details and 'full_name' in bb_repository_details['parent']:
                github_slug += '--forked-from--'+bb_repository_details['parent']['full_name'].replace('/', '-')
        else:
            github_slug = bb_repository_details['slug']

        # cap repo name length to 100 chars
        github_slug = github_slug[:100]

        return github_slug

    def call_git_subprocess(self, *args, cwd=None, error_message='', exit=True, retry=False):
        # set remote
        if cwd:
            try:
                os.makedirs(cwd)
            except FileExistsError:
                pass
        p=subprocess.Popen(['git'] + list(args), cwd=cwd)
        p.communicate()
        if p.returncode:
            print(error_message)
            if exit:
                if retry:
                    response = q.confirm('Would you like to retry this failed git command or exit? Y=retry, N=exit program:').ask()
                    if response:
                        return self.call_git_subprocess(*args, cwd=cwd, error_message=error_message, exit=exit, retry=retry)
                sys.exit(1)
            return False
        return True

    def get_github_pages_url(self, prefix=True, https=True):
        url = 'https://' if prefix and https else ('http://' if prefix else '')
        if self.__settings['github_pages_url_type'] == 0:
            url += '{}.github.io/{}'.format(str(self.__settings['github_owner']), str(self.__settings['github_pages_repo_name']))
        elif self.__settings['github_pages_url_type'] == 1:
            url += '{}'.format(str(self.__settings['github_pages_custom_url']))
        elif self.__settings['github_pages_url_type'] == 2:
            url += '{}/{}'.format(str(self.__settings['github_pages_custom_url']), str(self.__settings['github_pages_repo_name']))
        else:
            raise RuntimeError('Unknown option for self.__settings[\'github_pages_url_type\']')
        return url

    def __save_project_settings(self):
        self.__settings['__version__'] = software_version
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

        # Get additional usernames
        response = q.confirm('Would you like to add additional BitBucket accounts in order to parallelise the download of repositories? (only useful if you are downloading more than one repository - including forks)').ask()
        if response:
            self.__get_additional_bitbucket_credentials()

        # get a list of bitbucket repositories to save
        while not self.__get_bitbucket_repositories():
            pass

    def __get_additional_bitbucket_credentials(self):
        if self.__settings['bitbucket_additional_users']:
            print('You currently have the following BitBucket user accounts added (in addition to the master account):')
            for user in self.__settings['bitbucket_additional_users']:
                print(user)
        else:
            print('Would you like to add or remove an additional BitBucket account?')
        print('Note that all BitBucket accounts should have the same read permissions as the master BitBucket account')
        while True:
            choices = {"Add user account":0, "Remove user account":1, "Continue":2}
            response = q.select("What would you like to do?", choices=choices.keys()).ask()
            if choices[response] == 0:
                new_user = self.__get_bitbucket_credentials('')
                if new_user not in self.__settings['bitbucket_additional_users']:
                    self.__settings['bitbucket_additional_users'].append(new_user)
            elif choices[response] == 1:
                choices = {user:i for i,user in enumerate(self.__settings['bitbucket_additional_users'])}
                choices ["Cancel"] = len(choices.keys())
                response = q.select("Select user remove:", choices=choices.keys()).ask()
                if response == "Cancel":
                    pass
                elif response in choices.keys():
                    self.__settings['bitbucket_additional_users'].pop(choices[response])
                else:
                    raise RuntimeError('Unknown option selected')
            elif choices[response] == 2:
                return
            else:
                raise RuntimeError('Unknown option selected')
        

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

        # sort the list of repositories
        sorted_bb_repositories = sorted(bb_repositories, key=lambda repo: repo['name'])

        # list the repositories so they can be selected for migration
        choices = [q.Choice(repo['name'], checked=True if not self.__settings['bb_repositories_to_export'] else repo in self.__settings['bb_repositories_to_export']) for repo in sorted_bb_repositories]
        response = q.checkbox('Select repositories to export', choices=choices).ask()

        if len(response) == 0:
            print('You did not select any repositories to export. Please try again.')

        # save the list of repositories we are going to export
        self.__settings['bb_repositories_to_export'] = [repo for repo in sorted_bb_repositories if repo['name'] in response]

        return True

    def __get_backup_options(self):
        # self.__settings['backup_issues'] = q.confirm('Backup BitBucket issues as JSON files?', default=self.__settings['backup_issues']).ask()
        # if self.__settings['backup_issues']:
        #     self.__settings['generate_static_issue_pages'] = q.confirm('Generate new issue HTML pages for upload to a website?', default=self.__settings['generate_static_issue_pages']).ask()
        # else:
        #     self.__settings['generate_static_issue_pages'] = False

        # self.__settings['backup_pull_requests'] = q.confirm('Backup BitBucket pull requests as JSON files?', default=self.__settings['backup_pull_requests']).ask()
        # if self.__settings['backup_pull_requests']:
        #     self.__settings['generate_static_pull_request_pages'] = q.confirm('Generate new pull request HTML pages for upload to a website?', default=self.__settings['generate_static_pull_request_pages']).ask()
        # else:
        #     self.__settings['generate_static_pull_request_pages'] = False

        # self.__settings['backup_commit_comments'] = q.confirm('Backup BitBucket commit comments as JSON files?', default=self.__settings['backup_commit_comments']).ask()
        # if self.__settings['backup_commit_comments']:
        #     self.__settings['generate_static_commit_comments_pages'] = q.confirm('Generate new commit comments HTML pages for upload to a website?', default=self.__settings['generate_static_commit_comments_pages']).ask()
        # else:
        #     self.__settings['generate_static_commit_comments_pages'] = False

        self.__settings['backup_forks'] = q.confirm('Do you wish to recursively backup all repository forks?', default=self.__settings['backup_forks']).ask()

    def __get_github_import_options(self):
        choices = {
            "I need to create new repositories on GitHub for all previously selected BitBucket repositories":0, 
            "I already have repositories on GitHub for some of the BitBucket repositories I previously selected":1,
            "I don't want to import to GitHub":2,
        }
        response = q.select("How should we work with GitHub?", choices=choices.keys()).ask()
        if choices[response] == 0 or choices[response] == 1:
            self.__settings['import_to_github'] = True
            self.__get_master_github_credentials()
            
            # Get team/user where the repositories should be created
            self.__settings['github_owner'] = q.text('Enter the GitHub user or organisation that will own the new repositories?', default=self.__settings['github_owner']).ask()
            # Get list of GitHub repositories
            if choices[response] == 1:
                while not self.__get_github_repositories():
                    pass

            # TODO: don't use getcwd if setting is already set
            self.__settings['github_user_mapping_path'] = q.text('Enter the path to a JSON file containing username mappings between BitBucket and GitHub:', default=os.getcwd()).ask()
            # Hg to Git conversion tool
            choices = {
                "Use the GitHub source import tool":'github', 
                "Use my own local tool":'local',
            }
            self.__settings['hg_to_git_tool'] = choices[q.select("What would you like to use to convert the mercurial repositories to git repositories?", choices=choices.keys()).ask()]
            # Import issues to GitHub issues?
            self.__settings['github_import_issues'] = q.confirm('Import BitBucket issues to GitHub issues?', default=self.__settings['github_import_issues']).ask()
            # publish bitbucket backup?
            self.__settings['github_publish_pages'] = q.confirm('Publish BitBucket backup on GitHub pages (with links to current GitHub repository)?', default=self.__settings['github_publish_pages']).ask()
            if self.__settings['github_publish_pages']:
                while True:
                    self.__settings['github_pages_repo_name'] = q.text('Enter the repository name where you would like to publish the backup:', default=self.__settings['github_pages_repo_name']).ask()
                    if '/' in self.__settings['github_pages_repo_name']:
                        print('ERROR: repository names cannot have "/" characters in them. Make sure you are specifying the name without the GitHub user/org')
                    elif self.__settings['github_pages_repo_name'] is None:
                        print('Somehow you have specified type "None" for the repo name which is not allowed')
                        sys.exit(1)
                    elif self.__settings['github_pages_repo_name'] == '':
                        print('ERROR: You cannot specify an empty repository name')
                    else:
                        break
                choices = {
                    "No custom URL":0, 
                    "I have a custom domain for the specific repository":1,
                    "My user/organistion has a custom domain for all repositories":2,
                }
                response = q.select("Dow you wish to use a custom domain name for the BitBucket backup published to GitHub pages?", choices=choices.keys()).ask()
                self.__settings['github_pages_url_type'] = choices[response]
                if choices[response] == 1 or choices[response] == 2:
                    while True:
                        self.__settings['github_pages_custom_url'] = q.text('Enter the custom domain (without http(s):// prefix)', default=self.__settings['github_pages_custom_url']).ask()
                        if 'http://' in self.__settings['github_pages_custom_url'] or 'https://' in self.__settings['github_pages_custom_url']:
                            print('ERROR: Do not include the http(s):// prefix in the domain')
                        elif '/' in self.__settings['github_pages_custom_url']:
                            print('ERROR: Domains cannot have "/" characters in them')
                        elif self.__settings['github_pages_custom_url'] is None:
                            print('Somehow you have specified type "None" for the custom URL which is not allowed')
                        else:
                            break
                else:
                    self.__settings['github_pages_custom_url'] = ''

            # rewrite other repository URLS
            self.__settings['github_rewrite_additional_URLs'] = q.confirm('We will automatically rewrite any URLS in issues, pull-requests, etc that match any of the repositories you are migrating. Do you want to specify an additional list of URLs to rewrite?', default=self.__settings['github_rewrite_additional_URLs']).ask()
            if self.__settings['github_rewrite_additional_URLs']:
                # TODO: don't use getcwd if setting is already set
                self.__settings['github_URL_rewrite_file_path'] = q.text('Enter the path to a JSON file of the format {"<old BitBucket repo base URL>": ["<new BitBucket archive base URL>", "<new GitHub repo base URL>"], ...}:', default=os.getcwd()).ask()

            if self.__settings['backup_forks']:
                self.__settings['github_import_forks'] = q.confirm('Import BitBucket repository forks to Github (this is purely for preservation and will not be listed as forks on GitHub nor will git identify the forks as related in anyway to your new master repository)?', default=self.__settings['github_import_forks']).ask()
                if self.__settings['github_import_forks']:
                    if self.__settings['github_import_forks_to'] is None:
                        default_fork_destination = self.__settings['github_owner']
                    else:
                        default_fork_destination = self.__settings['github_import_forks_to']
                    self.__settings['github_import_forks_to'] = q.text('Which github organisation/user would you like the forks to be imported into?', default=default_fork_destination).ask()
                    # TODO: write this
                    # while not self.__get_github_repositories(forks=True):
                    #     pass
                    pass


        elif choices[response] == 2:
            self.__settings['import_to_github'] = False
            self.__settings.update({
                'master_github_username': '',
                'github_owner': '',
                'github_user_mapping_path': '',
                'bb_gh_user_mapping': {},
                'github_import_issues': False,
                'github_publish_pages': False,
                'github_pages_repo_name': '',
                'github_pages_url_type': 0,
                'github_pages_custom_url': '',
                'github_rewrite_additional_URLs': False,
                'github_URL_rewrite_file_path': '',
                'github_import_forks': False,
                'github_import_forks_to': None,
                'github_existing_repositories': {},
                'hg_to_git_tool': 'github',
                'local_git_repos_pushed': [],
            })
        else:
            raise RuntimeError('Unknown option selected')
    
    def __get_github_repositories(self, forks=False):
        looping = True
        # loop until user says "done"
        while looping:
            # list all selected BitBucket repositories in a choice (along with mapped GitHub repo)
            choices = {}
            for repository in self.__settings['bb_repositories_to_export']:
                if 'is_fork' in repository and repository['is_fork']:
                    continue
                text = 'BitBucket/'+repository['full_name']
                if repository['full_name'] in self.__settings['github_existing_repositories']:
                    text += ' (mapping to GitHub/{})'.format(self.__settings['github_existing_repositories'][repository['full_name']]['name'])
                choices[text] = repository['full_name']
            response = q.select("Select the BitBucket repository you want to map to an existing GitHub repository:", choices=choices.keys()).ask()
            repository_full_name = choices[response]
            # Ask use to type in the path to the matching GitHub repo
            existing_github_repo = ''
            if repository_full_name in self.__settings['github_existing_repositories']:
                existing_github_repo = self.__settings['github_existing_repositories'][repository_full_name]['name']
            github_slug = q.text('Enter the existing GitHub repository for BitBucket repository {} in the format <user or org.>/<repo name>:'.format(response), default=existing_github_repo).ask()

            # query githib for the repo details and save it
            github_auth = (self.__settings['master_github_username'], self.__get_password('github', self.__settings['master_github_username']))
            status, response = ghapi_json('repos/{repo}'.format(repo=github_slug), github_auth)
            if status == 200:
                self.__settings['github_existing_repositories'][repository_full_name] = {
                    'name': github_slug,
                    'repository': response,
                    'import_started': True,
                    'import_completed': True
                }
            else:
                print('ERROR: Failed to query {}. Are you sure that repository exists and you have permission to access it?'.format(gh_endpoint_to_full_url('repos/{repo}'.format(repo=github_slug))))
                print('')

            # ask the user if they want to do more
            choices = {
                "Edit another mapping between BitBucket and GitHub repositories":0, 
                "Continue with export":1,
            }
            response = q.select("What would you like to do?", choices=choices.keys()).ask()
            if choices[response] == 1:
                looping = False

        return True

    def __print_project_settings(self):
        print('Project settings:')
        print('    Name: {}'.format(self.__settings['project_name']))
        print('    Path: {}'.format(self.__settings['project_path']))
        print('    BitBucket username: {}'.format(self.__settings['master_bitbucket_username']))
        print('    Additional BitBucket accounts:')
        if self.__settings['bitbucket_additional_users']:
            for user in self.__settings['bitbucket_additional_users']:
                print('        {}'.format(user))
        else:
            print('        None')
        print('    Repositories to export:')
        for repo in self.__settings['bb_repositories_to_export']:
            if 'is_fork' in repo and repo['is_fork']:
                continue
            print('        {}'.format(repo['full_name']))
        # print('    Backup BitBucket issues: {}'.format(str(self.__settings['backup_issues'])))
        # print('        Generate HTML pages: {}'.format(str(self.__settings['generate_static_issue_pages'])))
        # print('    Backup BitBucket pull requests: {}'.format(str(self.__settings['backup_pull_requests'])))
        # print('        Generate HTML pages: {}'.format(str(self.__settings['generate_static_pull_request_pages'])))
        # print('    Backup BitBucket commit comments: {}'.format(str(self.__settings['backup_commit_comments'])))
        # print('        Generate HTML pages: {}'.format(str(self.__settings['generate_static_commit_comments_pages'])))
        print('    Backup forks: {}'.format(str(self.__settings['backup_forks'])))
        
        print('    Import to GitHub: {}'.format(str(self.__settings['import_to_github'])))
        if self.__settings['import_to_github']:
            print('        GitHub username: {}'.format(str(self.__settings['master_github_username'])))
            print('        GitHub owner: {}'.format(str(self.__settings['github_owner'])))
            print('        File containing mapping between BitBucket and GitHub users: {}'.format(str(self.__settings['github_user_mapping_path'])))
            print('        hg->git conversion tool: {}'.format(str(self.__settings['hg_to_git_tool'])))
            print('        Import issues to GitHub issue tracker: {}'.format(str(self.__settings['github_import_issues'])))
            print('        Publish BitBucket backup on GitHub pages: {}'.format(str(self.__settings['github_publish_pages'])))
            if self.__settings['github_publish_pages']:
                print('            Repository name for backup: {}'.format(str(self.__settings['github_pages_repo_name'])))
                print('            URL for backup: {}'.format(self.get_github_pages_url()))
            print('        Rewrite custom set of URLs in issues/comments/etc: {}'.format(str(self.__settings['github_rewrite_additional_URLs'])))
            if self.__settings['github_rewrite_additional_URLs']:
                print('            Path containing URL rewrites: {}'.format(str(self.__settings['github_URL_rewrite_file_path'])))
            print('        Import BitBucket forks to GitHub: {}'.format(str(self.__settings['github_import_forks'])))
            print('            Forks will be imported to GitHub user/organisation: {}'.format(str(self.__settings['github_import_forks_to'])))
            print('        These repositories are already on GitHub (including imports initiated by this script in previous runs:)')
            for bitbucket_name, repo in self.__settings['github_existing_repositories'].items(): 
                print('            BitBucket/{} -> GitHub/{}'.format(bitbucket_name, repo['name']))
            if not self.__settings['github_existing_repositories']:
                print('            None')

        response = q.confirm('Is this correct?').ask()
        return response

    def __get_master_bitbucket_credentials(self, force_new_password=False):
        self.__settings['master_bitbucket_username'] = self.__get_bitbucket_credentials(self.__settings['master_bitbucket_username'], force_new_password)
    
    def __get_master_github_credentials(self, force_new_password=False):
        self.__settings['master_github_username'] = self.__get_github_credentials(self.__settings['master_github_username'], force_new_password)

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
            try:
                password = self.__auth_credentials[service].get(username, None) or keyring.get_password(KEYRING_SERVICES[service], username)
            except keyring.errors.KeyringLocked:
                password = None
            
            if password is not None:
                # check the password works
                status, _ = SERVICE_CHECKS[service]((username, password))
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
            status, _ = SERVICE_CHECKS[service]((username, password))
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
    def __init__(self, owner, credentials, options, post_message, subset=None):
        self.__owner = owner
        self.__credentials = credentials
        self.__options = options
        self.__dummy_response_cache = {}
        self.__post_message = post_message

        self.__save_path = os.path.join(options['project_path'], 'bitbucket_data_raw')
        self.__save_path_relative = os.path.join(options['project_path'], 'gh-pages', 'data')

        self.__external_URL_rewrites = {}
        if options['github_rewrite_additional_URLs']:
            with open(options['github_URL_rewrite_file_path'], 'r') as f:
                self.__external_URL_rewrites = json.load(f)

        if subset is not None:
            self.__repos_to_export = [repo for repo in self.__options['bb_repositories_to_export'] if repo['full_name'] in subset]
        else:
            self.__repos_to_export = self.__options['bb_repositories_to_export']

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
        self.__repository_list = [tuple(repository['full_name'].split('/')) for repository in self.__options['bb_repositories_to_export']]
        mapping = [repo['full_name'] for repo in self.__options['bb_repositories_to_export']]
        # print(len(self.__repos_to_export))
        for repository in self.__repos_to_export:
            # this is a bit of a hack but whatever!
            self.__owner, self.__repository = repository['full_name'].split('/')
            self.__repo_full_name = repository['full_name']
            self.__files_downloaded = 0
            self.__duplicates_skipped = 0
            self.__already_downloaded = 0
            self.__time_of_last_update = time.time()-1
            self.__print_update()
            self.__backup_api()
            if ABORT_EVENT.is_set():
                return
            self.__print_update(end="\n", force=True)
            # clear the dummy response cache as we don't need it one we finish with a repository
            self.__dummy_response_cache = {}

            self.url_queue = queue.Queue()
            self.url_queue.put({'mapping':mapping})
            rewrite_count = 0
            # rewrite URLs in all files
            while not self.url_queue.empty() and not ABORT_EVENT.is_set():
                data = self.url_queue.get()
                self.make_urls_relative(**data)
                rewrite_count += 1
                self.__post_message('update', ('{repo}: Rewriting URLs in downloaded API data: {count} files rewritten'.format(repo=self.__repo_full_name, count=rewrite_count), "\r"))
            # self.make_urls_relative(mapping=mapping)
            if ABORT_EVENT.is_set():
                return
            self.__post_message('update', ('{repo}: Rewriting URLs in downloaded API data: {count} files rewritten'.format(repo=self.__repo_full_name, count=rewrite_count), "\n"))
            self.__post_message('complete', repository['full_name'])
            # reset the tree
            self.__tree = []
            self.__current_tree_location = ()
            self.tree_new_level()

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
            {'type': 'startswith', 'not':False, 'string':'repositories/{owner}/{repo}/refs/branches'.format(owner=self.__owner, repo=self.__repository)},
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
                    },
                    {
                        'params_match':{'sort':'*'}, 
                        'params_to_update':{'sort': 'created_on'},
                    },
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
                    {
                        'params_match':{'sort':'*'}, 
                        'params_to_update':{'sort': 'created_on'},
                    },
                ]  
            },
            # endpoints that take a max pagelen of 100 but don't have a page by default
            {
                'endpoint_match':[
                    'repositories/{owner}/{repo}/refs/tags'.format(owner=self.__owner, repo=self.__repository),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 100},
                    },
                ]  
            },
            # endpoints that take a max pagelen of 100 but don't have a page by default and should be sorted by creation date
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}/issues\/(\d+)\/changes(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/commits(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 100},
                    },
                    {
                        'params_match':{'sort':'*'}, 
                        'params_to_update':{'sort': 'created_on'},
                    },
                ]  
            },
            # endpoints that take a max pagelen of 100
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}/issues\/(\d+)\/attachments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    'repositories/{owner}/{repo}/components'.format(owner=self.__owner, repo=self.__repository),
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
            # endpoints that take a max pagelen of 100 and should be sorted by creation date
            {
                'endpoint_match':[
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/comments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/pullrequests\/(\d+)\/statuses(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/issues\/(\d+)\/comments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/commit\/(.+?)\/comments(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/commit\/(.+?)\/statuses(\?*)(?!\/).*'.format(owner=self.__owner, repo=self.__repository)),
                    re.compile(r'repositories\/{owner}\/{repo}/commits\/.*'.format(owner=self.__owner, repo=self.__repository)),
                    'repositories/{owner}/{repo}/commits'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/forks'.format(owner=self.__owner, repo=self.__repository),
                    'repositories/{owner}/{repo}/issues'.format(owner=self.__owner, repo=self.__repository),
                ], 
                'rewrites':[
                    {
                        'params_match':{'pagelen':None}, 
                        'params_to_update':{'pagelen': 100},
                    },
                    {
                        'params_match':{'page':None}, 
                        'params_to_update':{'page': 1},
                    },
                    {
                        'params_match':{'sort':'*'}, 
                        'params_to_update':{'sort': 'created_on'},
                    },
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

        self.url_queue = queue.Queue()
        self.url_queue.put(('https://api.bitbucket.org/2.0/repositories/{owner}/{repo}'.format(owner=self.__owner, repo=self.__repository), self.__tree))

        # Backup everything
        while not self.url_queue.empty() and not ABORT_EVENT.is_set():
            url, tree = self.url_queue.get()
            self.get_and_save_json(url, ignore_rules + pr_ignores, rewrite_rules, tree)
        self.tree_increment_level()

    @property
    def current_tree_location(self):
        return self.__current_tree_location

    @current_tree_location.setter
    def current_tree_location(self, value):
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
                        if match_param_value == '*':
                            continue
                        elif match_param_value is None:
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

    def __print_update(self, end="\r", force=False):
        if time.time()-self.__time_of_last_update > 0.25 or force:
            message = '{}/{}: Downloaded {} files ({} already downloaded, skipped {} duplicate URLs)'.format(self.__owner, self.__repository, self.__files_downloaded, self.__already_downloaded, self.__duplicates_skipped)
            self.__post_message('update', (message, end, force))
            # print(message, end=end)
            self.__time_of_last_update = time.time()

    def download_file(self, base_url, tree):
        # convert url to save path
        # remove '/' before the decode as the ones that exist prior to the decode as real characters
        #  (aka the '/' in the address, not query params) shouldn't be removed
        corrected_url_path = parse.unquote(base_url.replace(r'%2F', r'')).replace(bitbucket_api_url, '').replace('https://', '').replace('http://', '')
        # special_chars = ['?', ':', '\\', '*','<', '>', '"', '|']
        # for c in special_chars:
        #     corrected_url_path = corrected_url_path.replace(c,'')
        corrected_url_path = str(corrected_url_path).strip().replace(' ', '_')
        corrected_url_path = re.sub(r'(?u)[^-\w.\/\=\%\{\}]', '', corrected_url_path)
        save_path = os.path.join(self.__save_path, corrected_url_path)

        # save this URL in the tree
        # tree = self.__tree
        # for i in self.current_tree_location[:-1]:
        #     tree = tree[i]['children']
        tree.append({'url': base_url, 'rewritten_url': base_url, 'endpoint_path':save_path, 'already_processed': False, 'children': []})


        # don't download if it is already downloaded
        if os.path.exists(save_path):
            response = DummyResponse(save_path, self.__dummy_response_cache)
            if response.already_processed:
                self.__duplicates_skipped += 1
            else:
                self.__already_downloaded += 1
            # mark as already processed
            tree[-1]['already_processed'] = True
            # self.__already_downloaded += 1
            self.__print_update()
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

        DummyResponse(save_path, self.__dummy_response_cache)

        self.__files_downloaded += 1
        self.__print_update()

    def get_and_save_json(self, base_url, ignore_rules, rewrite_rules, tree):
        endpoint, params = full_url_to_query(base_url)
        endpoint = endpoint.replace(bitbucket_api_url, '')
        endpoint = endpoint.split('?')[0]
        # rewrite URL
        rewritten_endpoint, rewritten_params = self.rewrite_url(endpoint, params, rewrite_rules)
        encoded_rewritten_params = parse.urlencode(rewritten_params, doseq=True)

        # modify rewritten URL for save path (does not modify the URL being queried)
        endpoint_simplified_params = copy.deepcopy(rewritten_params)
        # we don't need the sort order in the save path
        if "sort" in endpoint_simplified_params:
            del endpoint_simplified_params['sort']
        # I think that some API urls use ctx for pagination
        # If so, we don't want to delete the ctx if there is no other indication of pagination
        if "page" in endpoint_simplified_params and "ctx" in endpoint_simplified_params:
            del endpoint_simplified_params['ctx']
        # This information is stored inside the file anyway, and every URL should be being grabbed with the 
        # largest number of items per page anyway (to reduce the number of API calls we need to make)
        if "pagelen" in endpoint_simplified_params:
            del endpoint_simplified_params['pagelen']
        endpoint_simplified_params_str = parse.urlencode(endpoint_simplified_params, doseq=True)
        endpoint_path = os.path.join(self.__save_path, rewritten_endpoint)
        if endpoint_simplified_params_str:
            endpoint_path += '_'
            endpoint_path += endpoint_simplified_params_str
        endpoint_path += ".json"

        # create new URL to query
        rewritten_base_url = bitbucket_api_url + rewritten_endpoint
        if encoded_rewritten_params:
            rewritten_base_url += '?' + encoded_rewritten_params

        # save this URL in the tree
        # tree = self.__tree
        # for i in self.current_tree_location[:-1]:
        #     tree = tree[i]['children']
        tree.append({'url': base_url, 'rewritten_url': rewritten_base_url, 'endpoint_path':endpoint_path, 'already_processed': False, 'children': []})

        # create the dir structure
        head, _ = os.path.split(endpoint_path)
        try:
            os.makedirs(head)
        except FileExistsError:
            pass

        if os.path.exists(endpoint_path):
            # load the file
            response = DummyResponse(endpoint_path, self.__dummy_response_cache)
            if response.already_processed:
                # mark as already processed
                tree[-1]['already_processed'] = True
                self.__duplicates_skipped += 1
                self.__print_update()
                return
            else:
                self.__already_downloaded += 1
        else:
            response = bb_query_api(rewritten_base_url, auth=self.__credentials)
            self.__files_downloaded += 1
            self.__print_update()

        if response.status_code == 200:
            # save the data
            try:
                json_data = response.json()
            except BaseException:
                # print('Not a JSON response, ignoring')
                # print('     original endpoint:', base_url)
                # print('    rewritten endpoint:', rewritten_base_url)
                # print('    data:', response.text)
                self.__files_downloaded -= 1
                self.__print_update(force=True)
                return
        
            with open(endpoint_path, 'w') as f:
                json.dump(json_data, f)

            # Create dummy response now so that we don't think this file was downloaded on a previous run of the script
            # next time it is encountered on this run of the script
            DummyResponse(endpoint_path, self.__dummy_response_cache)

            self.tree_new_level()

            # get the other pages
            if "next" in json_data:
                self.url_queue.put((json_data['next'], tree[-1]['children']))
                # self.get_and_save_json(json_data['next'], ignore_rules, rewrite_rules, tree[-1]['children'])
                self.tree_increment_level()

            # download any files references
            for compiled_regex in self.file_download_regexes:
                results = compiled_regex.findall(response.text)
                for result in results:
                    try:
                        # print('downloading file: {}'.format(result))
                        self.download_file(result, tree[-1]['children'])
                        self.tree_increment_level()
                    except BaseException:
                        self.__post_message('update', ('{}: Failed to download file {}'.format(self.__repo_full_name, result), "\n"))
                        # print('Failed to download file {}'.format(result))
                        raise

            # find all the other referenced API endpoints in this data and collect them too
            results = prog.findall(response.text)
            for result in results:
                # hack because nothing references issue/<num>/changes for some reason
                issue_pattern = r'repositories/{}/{}/issues/(\d+)$'.format(self.__owner, self.__repository)
                matches = re.match(issue_pattern, result)
                if matches:
                    self.url_queue.put((bb_endpoint_to_full_url(result+'/changes'), tree[-1]['children']))
                    # self.get_and_save_json(bb_endpoint_to_full_url(result+'/changes'), ignore_rules, rewrite_rules, tree[-1]['children'])
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

                # TODO: Work out why this is needed now and wasn't needed for other repositories I tested with
                #       Was it because I had two repositories 'user/repo" and "user/repo-dev"?
                #       In that case it started download repo-dev as part of repo and the ignore rules didn't
                #       match so it started downloading source files too.
                repo_str = 'repositories/{}/{}'.format(self.__owner, self.__repository)
                if not skip and not result.startswith(repo_str+'/') and result != repo_str:
                    skip=True
                    print('Skipping file {} (found in {})'.format(result, endpoint_path))

                if skip:
                    continue

                self.url_queue.put((bb_endpoint_to_full_url(result), tree[-1]['children']))
                # self.get_and_save_json(bb_endpoint_to_full_url(result), ignore_rules, rewrite_rules, tree[-1]['children'])
                self.tree_increment_level()
            
            self.tree_finished_level()

        elif response.status_code == 401:
            self.__post_message('update', ('{repo}: ERROR: Access denied for endpoint {endpoint}. No data was saved. Check your credentials and access permissions.'.format(repo=self.__repo_full_name, endpoint=rewritten_endpoint), "\n"))
            # print("ERROR: Access denied for endpoint {endpoint}. No data was saved. Check your credentials and access permissions.".format(endpoint=rewritten_endpoint))
            self.__files_downloaded -= 1
            self.__print_update(force=True)
        elif response.status_code == 404:
            self.__post_message('update', ('{repo}: ERROR: API endpoint {endpoint} doesn\'t exist'.format(repo=self.__repo_full_name, endpoint=rewritten_endpoint), "\n"))
            # print("ERROR: API endpoint {endpoint} doesn't exist".format(endpoint=rewritten_endpoint, repo=self.__repository))
            self.__files_downloaded -= 1
            self.__print_update(force=True)
        else:
            self.__post_message('update', ('{repo}: ERROR: Unexpected response code {code} for endpoint {endpoint}'.format(repo=self.__repo_full_name, code=response.status_code, endpoint=rewritten_endpoint), "\n"))
            # print("ERROR: Unexpected response code {code} for endpoint {endpoint}".format(code=response.status_code, endpoint=rewritten_endpoint))
            self.__files_downloaded -= 1
            self.__print_update(force=True)

    def make_urls_relative(self, tree=None, parent_percent=0, parent_percent_subset=100.0, mapping=None):
        # tree.append({'url': base_url, 'rewritten_url': rewritten_base_url, 'endpoint_path':endpoint_path, 'already_processed': False, 'children': []})
        
        top_level = False
        if tree is None:
            tree = self.__tree
            top_level = True
            # self.__post_message('update', ('{repo}: Rewriting URLs in downloaded API data: {pcnt:.1f}% complete'.format(repo=self.__repo_full_name, pcnt=parent_percent), "\r"))
            # print('Rewriting URLs in downloaded API data: {:.1f}% complete'.format(parent_percent), end="\r")
        if len(tree):
            parent_percent_subset = parent_percent_subset/len(tree)

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
            # only process the items that have children (we may encounter reference to a file that was marked as already processed)
            # before we hit the reference that was not marked as already processed.
            if item['already_processed'] and new_path.endswith('.json'):
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
                        # data = data.replace('![]({})'.format(child['url']), '![]({})'.format(new_url)) # markdown image format
                        data = re.sub(r"\!\[(.*?)\]\("+re.escape(child['url'])+r"\)", r"![\1]("+new_url+r")", data, flags=re.MULTILINE) # markdown image format

                    # fix weird URLS that exist which aren't valid api endpoints, but BitBucket puts them in the content...WTF?
                    # data = re.sub(r'\\\"(https\:\/\/api\.bitbucket\.org\/(.*?)\/(.*?)\/(.*?))\\\"', self.fix_stupid_bitbucket_urls, data, flags=re.MULTILINE)
                    data = re.sub(r'\\\"(https\:\/\/api\.bitbucket\.org\/(.*?)\/(.*?)((\\\")|(\/(.*?))\\\"))', self.fix_stupid_bitbucket_urls, data, flags=re.MULTILINE)
                    data = re.sub(r'(\\\"\/.*?(\&\#109;\&\#97;\&\#105;\&\#108;\&\#116;\&\#111;\&\#58;)(.*?)\\\")', self.fix_stupid_bitbucket_email_links, data, flags=re.MULTILINE)

                    # apply relevant BB to GH transformation
                    # find repo name
                    # apply all transformations 
                    for name in mapping:
                        data = data.replace('https://bitbucket.org/{}'.format(name), '#!/{}'.format(name))

                    for old_url, (new_url, _) in self.__external_URL_rewrites.items():
                        data = data.replace(old_url, new_url)

                    # save file
                    with open(new_path, 'w') as f:
                        f.write(data)
                # if it is a binary file
                else:
                    shutil.copyfile(item['endpoint_path'], new_path)

            # recurse over children
            self.url_queue.put({
                'tree': item['children'],
                'mapping': mapping,
                'parent_percent': parent_percent, 
                'parent_percent_subset': parent_percent_subset,
            })
            # self.make_urls_relative(item['children'], parent_percent=parent_percent, parent_percent_subset=parent_percent_subset, mapping=mapping)
            parent_percent += parent_percent_subset
            # self.__post_message('update', ('{repo}: Rewriting URLs in downloaded API data: {pcnt:.1f}% complete'.format(repo=self.__repo_full_name, pcnt=parent_percent), "\r"))
            # print('Rewriting URLs in downloaded API data: {:.1f}% complete'.format(parent_percent), end="\r")

        # if top_level:
        #     self.__post_message('update', ('{repo}: Rewriting URLs in downloaded API data: 100.0% complete'.format(repo=self.__repo_full_name), "\n"))
            # print('Rewriting URLs in downloaded API data: 100.0% complete')

    def fix_stupid_bitbucket_urls(self, matchobj):
        # If the URL matches one of the respositories we are backing up, rewrite it to point to the correct
        # static HTML page
        if (matchobj.group(2), matchobj.group(3)) in self.__repository_list:
            return r'\"#!/{m2}/{m3}{m4}'.format(m2=matchobj.group(2), m3=matchobj.group(3), m4=matchobj.group(4))
        else:
            # it's a link to repository that we are not backing up. We'll redirect it to the actual bitbucket website for posterity,
            # although it is unlikely the URL will exist beyond the BitBucket shutdown. It's possible the owner will put up a redirect
            # at some point though!
            if 'https://api.bitbucket.org/2.0/' not in matchobj.group(0) and 'https://api.bitbucket.org/1.0/' not in matchobj.group(0):
                return matchobj.group(0).replace('https://api.bitbucket.org', 'https://bitbucket.org')
            # otherwise it's an API endpoint that exists in HTML code. So we'll leave it, as it was probably put there deliberately by a user, not the BitBucket API
            return matchobj.group(0)

    def fix_stupid_bitbucket_email_links(self, matchobj):
        return r'\"mailto:{}\"'.format(html.unescape(matchobj.group(3)))


class DummyResponse(object):
    def __init__(self, path, cache):
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

    def __new__(cls, path, cache, *args, **kwargs):
        existing = cache.get(path, None)
        if existing is not None:
            # print('ignoring',path)
            existing.already_processed = True
            return existing
        obj = super(DummyResponse, cls).__new__(cls)
        cache[path] = obj
        return obj

def main():
    project = MigrationProject()

if __name__ == "__main__":
    main()
