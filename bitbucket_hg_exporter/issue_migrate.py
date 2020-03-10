# Much of this code is from the bitbucket-issue-migration project at
#   https://github.com/jeffwidman/bitbucket-issue-migration.
#   copyright Jeff Widman (https://github.com/jeffwidman) 2013-1019
#   licensed under the GPLv3
#   The code has been modified by Philip Starkey, 2020 for the
#   Bitbucket Hg Exporter project at https://github.com/philipstarkey/bitbucket-hg-exporter
#   as permitted under the GPLv3 license
#
# New functions and changes copyright Philip Starkey 2020 and licensed under the GPLv3

import json
import os
import re
import time
import urllib.parse

import requests
import questionary as q

SEP = "-" * 40

ISSUE_TEMPLATE = """\
**Original report ([{issue_link_type} issue]({archive_url}/{repo}/issues/{id})) by {reporter}.**
{attachments}
{sep}
{content}
"""

ATTACHMENTS_TEMPLATE = """
The original report had attachments: {attach_names}
"""

COMMENT_TEMPLATE = """\
**Original comment by {author}.**

{sep}
{changes}{content}
"""
class Options(object):
    pass


####
# THINGS TO FIX
#   * need to check what happens to inter/intra repository links (how they are rewritten) in the raw markup
#   * assignee requires the user be a collaborator, so is disabled for now
#####

def import_issues_to_github(bb_repo, gh_repo, gh_auth, settings, mapping, dry_run=True):
    options = Options()
    options.bitbucket_repo = bb_repo
    options.github_repo = gh_repo
    options.gh_auth = gh_auth
    options.settings = settings
    options.mapping = mapping
    options.dry_run = dry_run

    # check that there are no issues/pullrequests on GitHub for this repository. If there are,
    # we should abort as we can't yet handle this case!
    offset = 0
    if not options.dry_run:
        response = requests.get('https://api.github.com/search/issues?q=repo:{}+sort:author-date-desc&sort=created&order=desc'.format(options.github_repo), auth=options.gh_auth)
        if response.status_code == 200:
            data = response.json()
            if len(data['items']) != 0:
                cont = q.confirm('WARNING: The GitHub repo {} has existing issues or pull requests. If these are just issues imported from a previous run of this tool, answer "Y" to continue with the import from the point we left off. If they are new issues/pull requests, answer "N" to skip importing. Do you want to continue importing?'.format(options.github_repo), default=False).ask()
                # print('WARNING: Skipping import of issues to GitHub {} as there are existing issues or pull requests in the repository.'.format(options.github_repo))
                if not cont:
                    return False
                else:
                    offset = data['items'][0]['number']
        else:
            print('WARNING: Skipping import of issues to GitHub {} as we could not query the current list of issues.'.format(options.github_repo))
            return False
    else:
        project_base = options.settings['project_path']
        temp_dir = os.path.join(project_base, 'temp', 'gh_issues', *options.bitbucket_repo.split('/'))
        try:
            os.makedirs(temp_dir)
        except FileExistsError:
            pass

    # GitHub's Import API currently requires a special header
    headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
    gh_milestones = GithubMilestones(options.github_repo, options.gh_auth, headers)
    
    issues_iterator = fill_gaps(get_issues(options))

    for index, issue in enumerate(issues_iterator):
        # skip issues already imported
        if issue['id'] <= offset:
            continue
        if isinstance(issue, DummyIssue):
            comments = []
            changes = {}
            attachments = []
        else:
            comments = get_issue_comments(issue['id'], options)
            changes = {change['id']: change for change in get_issue_changes(issue['id'], options)}
            attachments = get_attachment_names(issue['id'], options)

        gh_issue = convert_issue(issue, comments, changes.values(), options, attachments, gh_milestones)
        gh_comments = [convert_comment(comment, changes.get(comment['id'], {}).get('changes', {}), options) for comment in comments]
            
        if options.dry_run:
            with open(os.path.join(temp_dir, 'ghissue_{}.json'.format(issue['id'])), 'w') as f:
                json.dump({'issue': gh_issue, 'comments': gh_comments}, f, indent=4)
        else:
            push_respo = push_github_issue(
                gh_issue, gh_comments, options.github_repo,
                options.gh_auth, headers
            )
            # issue POSTed successfully, now verify the import finished before
            # continuing. Otherwise, we risk issue IDs not being sync'd between
            # Bitbucket and GitHub because GitHub processes the data in the
            # background, so IDs can be out of order if two issues are POSTed
            # and the latter finishes before the former. For example, if the
            # former had a bunch more comments to be processed.
            # https://github.com/jeffwidman/bitbucket-issue-migration/issues/45
            status_url = push_respo.json()['url']
            resp = verify_github_issue_import_finished(
                status_url, options.gh_auth, headers)

            # Verify GH & BB issue IDs match.
            # If this assertion fails, convert_links() will have incorrect
            # output.  This condition occurs when:
            # - the GH repository has pre-existing issues.
            # - the Bitbucket repository has gaps in the numbering.
            if resp:
                gh_issue_url = resp.json()['issue_url']
                gh_issue_id = int(gh_issue_url.split('/')[-1])
                assert gh_issue_id == issue['id']
    return True

class DummyIssue(dict):
    def __init__(self, num):
        self.update(id=num)

def fill_gaps(issues):
    current_id = 0
    for issue in issues:
        issue_id = issue['id']
        for dummy_id in range(current_id + 1, issue_id):
            yield DummyIssue(dummy_id)
        current_id = issue_id
        yield issue

def get_issues(options):
    return __get_items_from_file('issues_page=1.json', options)

def get_issue_comments(issue_id, options):
    comment_path = os.path.join('issues', '{id}'.format(id=issue_id), 'comments_page=1.json')
    return __get_items_from_file(comment_path, options)

def get_issue_changes(issue_id, options):
    change_path = os.path.join('issues', '{id}'.format(id=issue_id), 'changes.json')
    return __get_items_from_file(change_path, options)

def get_attachment_names(issue_id, options):
    """Get the attchments data for this issue"""
    # project_base = options.settings['project_path']
    # repo_base = os.path.join(project_base, 'gh-pages',)
    # file_path = os.path.join('data', 'repositories', *options.bitbucket_repo.split('/'), 'issues', '{}'.format(issue_num), 'attachments_page=1.json')
    # attachments = []
    # more = True
    # while more:
    #     with open(os.path.join(repo_base, file_path), 'r') as f:
    #         data = json.load(f)
    #         attachments.extend(data['values'])
    #         if 'next' in data:
    #             file_path = data['next']
    #         else:
    #             more = False
    # return attachments
    attachment_path = os.path.join('issues', '{id}'.format(id=issue_id), 'attachments_page=1.json')
    return __get_items_from_file(attachment_path, options)

def __get_items_from_file(start_file, options):
    project_base = options.settings['project_path']
    repo_base = os.path.join(project_base, 'gh-pages')
    file_path = os.path.join('data', 'repositories', *options.bitbucket_repo.split('/'), start_file)
    more = True
    while more:
        try:
            with open(os.path.join(repo_base, file_path), 'r') as f:
                data = json.load(f)
            for item in data['values']:
                yield item

            if 'next' in data:
                file_path = data['next']
            else:
                more = False
        except FileNotFoundError:
            return
        except BaseException:
            print('Error while loading file {}'.format(os.path.join(repo_base, file_path)))
            raise

def convert_issue(issue, comments, changes, options, attachments, gh_milestones):
    """
    Convert an issue schema from Bitbucket to GitHub's Issue Import API
    """
    # Bitbucket issues have an 'is_spam' field that Akismet sets true/false.
    # they still need to be imported so that issue IDs stay sync'd

    if isinstance(issue, DummyIssue):
        return dict(
            title="Deleted issue",
            body="This issue was deleted on BitBucket and was thus not migrated to GitHub",
            closed=True,
        )
    labels = [issue['priority']]

    for key in ['component', 'kind', 'version']:
        v = issue[key]
        if v is not None:
            if key == 'component' or key == 'version':
                v = v['name']
            # Commas are permitted in Bitbucket's components & versions, but
            # they cannot be in GitHub labels, so they must be removed.
            # Github caps label lengths at 50, so we truncate anything longer
            labels.append(v.replace(',', '')[:50])

    is_closed = issue['state'] not in ('open', 'new', 'on hold')
    out = {
        'title': issue['title'],
        'body': format_issue_body(issue, attachments, options),
        'closed': is_closed,
        'created_at': convert_date(issue['created_on']),
        'updated_at': convert_date(issue['updated_on']),
        'labels': labels,
    }

    # Assign issue if we have a mapping between BitBucket and GitHub users for the relevant user
    if issue['assignee'] and issue['assignee']['nickname'] in options.settings['bb_gh_user_mapping']:
        # out['assignee'] = options.settings['bb_gh_user_mapping'][issue['assignee']['nickname']]
        pass

    if is_closed:
        closed_status = [
            convert_date(change['created_on'])
            for change in changes
            if 'state' in change['changes'] and
            change['changes']['state']['old'] in
            ('', 'open', 'new', 'on hold') and
            change['changes']['state']['new'] not in
            ('', 'open', 'new', 'on hold')
        ]
        if closed_status:
            out['closed_at'] = sorted(closed_status)[-1]
        else:
            out['closed_at'] = issue['updated_on']

    # If there's a milestone for the issue, convert it to a Github
    # milestone number (creating it if necessary).
    milestone = issue['milestone']
    if milestone and milestone['name']:
        out['milestone'] = gh_milestones.ensure(milestone['name'])

    return out

def convert_comment(comment, changes, options):
    """
    Convert an issue comment from Bitbucket schema to GitHub's Issue Import API
    schema.
    """
    return {
        'created_at': convert_date(comment['created_on']),
        'body': format_comment_body(comment, changes, options),
    }

def convert_date(bb_date):
    """Convert the date from Bitbucket format to GitHub format."""
    # '2012-11-26T09:59:39+00:00'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d)T(\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError("Could not parse date: {}".format(bb_date))

def format_user(user, options):
    """
    Format a Bitbucket user's info into a string containing either 'Anonymous'
    or their name and links to their Bitbucket and GitHub profiles.
    """
    # anonymous comments have null 'author_info', anonymous issues don't have
    # 'reported_by' key, so just be sure to pass in None
    if user is None:
        return "Anonymous"
    if not isinstance(user, dict):
        user = {'nickname': user, 'display_name': user}
    profile_url = "https://bitbucket.org/{0}".format(user['nickname'])
    if "links" in user and "html" in user['links'] and "href" in user['links']['html']:
        profile_url = user['links']['html']['href']
    bb_user = "Bitbucket: [{0}]({1})".format(user['nickname'], profile_url)
    if user['nickname'] in options.settings['bb_gh_user_mapping']:
        gh_username = options.settings['bb_gh_user_mapping'][user['nickname']]
        gh_user = ", GitHub: [{0}](https://github.com/{0})".format(gh_username)
    else:
        gh_user = ""
    return (user['display_name'] + " (" + bb_user + gh_user + ")")


def format_issue_body(issue, attachments, options):
    content = issue['content']['raw']
    content = apply_conversion(content, options, issue['id'])

    if options.settings['github_publish_pages']:
        # repo URL (for attachment links)
        archive_url = 'https://{owner}.github.io/{repo}'.format(owner=options.settings['github_owner'], repo=options.settings['github_pages_repo_name'])
        # link to archived attachments
        attach_names = ["[{name}]({archive_url}/{url})".format(name=val['name'], url=val['links']['self']['href'][0], archive_url=archive_url) for val in attachments]
        attach_names = ", ".join(attach_names)
        attachments = ATTACHMENTS_TEMPLATE.format(attach_names=attach_names) if attach_names else ''

        # Append necessary hashbang for link to actual archive pages
        archive_url += '/#!'
    else:
        attach_names = [val['name'] for val in attachments]
        attachments = ATTACHMENTS_TEMPLATE.format(attach_names=", ".join(attach_names)) if attach_names else ''
        archive_url = 'https://bitbucket.org'

    data = dict(
        # anonymous issues are missing 'reported_by' key
        reporter=format_user(issue.get('reporter'), options),
        sep=SEP,
        repo=options.bitbucket_repo,
        archive_url=archive_url,
        issue_link_type='archived' if options.settings['github_publish_pages'] else 'BitBucket',
        id=issue['id'],
        content=content,
        attachments=attachments
    )
    return ISSUE_TEMPLATE.format(**data)

def format_comment_body(comment, changes, options):
    content = comment['content']['raw']
    if content is None:
        content = ""
    content = apply_conversion(content, options, comment['issue']['id'])
    author = comment['user']

    change_str = "".join(
            "* {}".format(formatted) for formatted in [
                format_change_element(change_type, change, options)
                for change_type, change in changes.items()
            ] if formatted
        )
    if change_str:
        change_str = "\n" + change_str + "\n"

    data = dict(
        author=format_user(author, options),
        sep=SEP,
        content=content,
        changes=change_str,
    )
    return COMMENT_TEMPLATE.format(**data)

image_regex = re.compile(r'\!\[\]\((.*?)\)', re.MULTILINE)
def apply_conversion(content, options, issue_id):
    # first apply the conversion for this repository, then for all the other ones
    if options.bitbucket_repo in options.mapping:
        content = options.mapping[options.bitbucket_repo].convert_all(content)
    else:
        print('WARNING: could not find Bb2Gh object for repo {}'.format(options.bitbucket_repo))
    for repo, mapping in options.mapping.items():
        if repo == options.bitbucket_repo:
            continue
        else:
            content = mapping.convert_other_repo_content(content)

    image_paths = image_regex.findall(content)
    if options.settings['github_publish_pages']:
        archive_url = 'https://{owner}.github.io/{repo}'.format(owner=options.settings['github_owner'], repo=options.settings['github_pages_repo_name'])
        for match in image_paths:
            if os.path.exists(os.path.join(options.settings['project_path'], 'gh-pages', *match.split('/'))):
                content = content.replace('![]({})'.format(match), '![]({archive_url}/{match})'.format(match=urllib.parse.quote(match), archive_url=archive_url))
    elif image_paths:
        for match in image_paths:
            if os.path.exists(os.path.join(options.settings['project_path'], 'gh-pages', *match.split('/'))):
                content = content.replace('![]({})'.format(match), '![](https://{match})'.format(match=urllib.parse.quote(match[5:])))
        print('Warning: {repo} issue #{id} contains one or more images that are stored on BitBucket. They may not survive when BitBucket deletes your repository. You will need to manually fix this or configure this tool to publish an archive of your repository data on GitHub pages.'.format(id=issue_id, repo=options.bitbucket_repo))

    return content

def format_change_element(change_type, change, options):
    # we don't want to show the whole old and new description again
    if change_type == 'content':
        return 'Edited issue description'

    # get old/new states
    old = change.get('old', '')
    new = change.get('new', '')

    # Format username with link if the change type is assignee
    if change_type == 'assignee':
        if old:
            old = format_user(old, options)
        if new:
            new = format_user(new, options)

    # return formated string
    if old and new:
        return 'changed {} from "{}" to "{}"\n'.format(change_type, old, new)
    elif old:
        return 'removed "{}" {}\n'.format(old, change_type)
    elif new:
        return 'set {} to "{}"\n'.format(change_type, new)
    else:
        return None

def push_github_issue(issue, comments, github_repo, auth, headers):
    """
    Push a single issue to GitHub.
    Importing via GitHub's normal Issue API quickly triggers anti-abuse rate
    limits. So we use their dedicated Issue Import API instead:
    https://gist.github.com/jonmagic/5282384165e0f86ef105
    https://github.com/nicoddemus/bitbucket_issue_migration/issues/1
    """
    issue_data = {'issue': issue, 'comments': comments}
    url = 'https://api.github.com/repos/{repo}/import/issues'.format(
        repo=github_repo)
    respo = requests.post(url, json=issue_data, auth=auth, headers=headers)
    if respo.status_code == 202:
        return respo
    elif respo.status_code == 422:
        raise RuntimeError(
            "Initial import validation failed for issue '{}' due to the "
            "following errors:\n{}".format(issue['title'], respo.json())
        )
    else:
        raise RuntimeError(
            "Failed to POST issue: '{}' due to unexpected HTTP status code: {}"
            .format(issue['title'], respo.status_code)
        )

def verify_github_issue_import_finished(status_url, auth, headers):
    """
    Check the status of a GitHub issue import.
    If the status is 'pending', it sleeps, then rechecks until the status is
    either 'imported' or 'failed'.
    """
    while True:  # keep checking until status is something other than 'pending'
        respo = requests.get(status_url, auth=auth, headers=headers)
        if respo.status_code in (403, 404):
            print(respo.status_code, "retrieving status URL", status_url)
            respo.status_code == 404 and print(
                "GitHub sometimes inexplicably returns a 404 for the "
                "check url for a single issue even when the issue "
                "imports successfully. For details, see #77."
            )
            pprint.pprint(respo.headers)
            return
        if respo.status_code != 200:
            raise RuntimeError(
                "Failed to check GitHub issue import status url: {} due to "
                "unexpected HTTP status code: {}"
                .format(status_url, respo.status_code)
            )
        status = respo.json()['status']
        if status != 'pending':
            break
        time.sleep(1)
    if status == 'imported':
        print("Imported Issue:", respo.json()['issue_url'])
    elif status == 'failed':
        raise RuntimeError(
            "Failed to import GitHub issue due to the following errors:\n{}"
            .format(respo.json())
        )
    else:
        raise RuntimeError(
            "Status check for GitHub issue import returned unexpected status: "
            "'{}'"
            .format(status)
        )
    return respo

class GithubMilestones(object):
    """
    This class handles creation of Github milestones for a given
    repository.
    When instantiated, it loads any milestones that exist for the
    respository. Calling ensure() will cause a milestone with
    a given title to be created if it doesn't already exist. The
    Github number for the milestone is returned.
    """

    def __init__(self, repo, auth, headers):
        self.url = 'https://api.github.com/repos/{repo}/milestones'.format(repo=repo)
        self.session = requests.Session()
        self.session.auth = auth
        self.session.headers.update(headers)
        self.refresh()

    def refresh(self):
        self.title_to_number = self.load()

    def load(self):
        milestones = {}
        url = self.url + "?state=all"
        while url:
            respo = self.session.get(url)
            if respo.status_code != 200:
                raise RuntimeError(
                    "Failed to get milestones due to HTTP status code: {}".format(
                    respo.status_code))
            for m in respo.json():
                milestones[m['title']] = m['number']
            url = respo.links.get("next")
        return milestones

    def ensure(self, title):
        number = self.title_to_number.get(title)
        if number is None:
            number = self.create(title)
            self.title_to_number[title] = number
        return number

    def create(self, title):
        respo = self.session.post(self.url, json={"title": title})
        if respo.status_code != 201:
            raise RuntimeError(
                "Failed to get milestones due to HTTP status code: {}".format(
                respo.status_code))
        return respo.json()["number"]