# The memoize, get_bb_username functions and BbToGh class are from the project at
#   https://github.com/sphinx-doc/bitbucket_issue_migration,
#   copyright Takayuki SHIMIZUKAWA <https://github.com/shimizukawa> 2015
#   licensed under the GPLv3.
#   The project was a fork of https://github.com/haysclark/bitbucket_issue_migration which 
#   is in turn a fork of https://github.com/jeffwidman/bitbucket-issue-migration.
# These functions and class have been modified by Philip Starkey, 2019 for the
#   Bitbucket Hg Exporter project at https://github.com/philipstarkey/bitbucket-hg-exporter
#
# The remaining contents of this file were inspired by the same project, however, the original code 
# was not Python 3 compatible, nor efficient in it's use of subprocesses.
# As such, it was rewritten from scratch for this project.
#
# New functions and changes copyright Philip Starkey 2019 and licensed under the GPLv3

import subprocess
import uuid
import json
import sys
import re
import bisect
import urllib.parse as urlparse
import logging
import datetime
import argparse

import requests
import dateutil.parser

class memoize(object):
    def __init__(self):
        self.cache = {}

    def make_key(self, *args, **kw):
        key = "-".join(str(a) for a in args)
        key += "-".join(str(k) + "=" + str(v) for k, v in kw.items())
        return key

    def __call__(self, func):
        def wrap(*args, **kw):
            key = self.make_key(*args, **kw)
            if key in self.cache:
                return self.cache[key]
            res = func(*args, **kw)
            self.cache[key] = res
            return res

        return wrap


@memoize()
def get_bb_username(user, try_with_braces=False):
    # fmt: off
    if user in ('name', 'names', 'class', 'import', 'property', 'ubuntu', 'wrap',
                'github', 'for', 'enumerate', 'item', 'itemize', 'type', 'title',
                'empty', 'replace', 'gmail', 'id', 'href', 'app', 'echo'):
        #logging.info('user @%s is skipped. It\'s a some code.', user)
        return False
    # fmt: on
    if try_with_braces:
        user = '{'+user+'}'
    base_user_api_url = "https://bitbucket.org/api/2.0/users/"
    res = requests.get(base_user_api_url + user)
    if res.status_code == 200 or res.status_code == 304:
        #logging.debug("user @%s is exist in BB.", user)
        return res.json()
        #logging.debug("user @%s is not found in BB.", user)
        if not try_with_braces:
            return get_bb_username(user, try_with_braces=True)
        return None


class BbToGh(object):
    """
    Convert BB links and changeset markers in the issues.json
    * Normalize BB old URLs.
    * Convert BB changeset marker into GH.
    * Convert BB changeset links into GH.
    * Convert BB issue links into GH.
    * Convert BB src links into GH.

    TODO:
    * Convert markup-less commit hashes to gh-hash
    * use user mappings
    * add a shorter version of convert_all which rewrites relevant repo data in content from *other* repos
    """

    def __init__(self, hg_logs, git_logs, bb_url, gh_url, user_mapping, archive_url=None, known_hg_git_mapping=None):
        self.bb_url = bb_url.rstrip("/")
        self.gh_url = gh_url.rstrip("/")
        self.gh_repo = self.gh_url.replace('https://github.com/', '')
        self.bb_repo = self.bb_url.replace('https://bitbucket.org/', '')
        self.hg_to_git = {}
        self.hg_dates = {}
        self.hg_branches = {}
        self.hg_revnum_to_hg_node = {}
        self.user_mapping = user_mapping
        self.archive_url = None
        self.base_archive_url = None
        if archive_url is not None:
            self.archive_url = archive_url.rstrip("/")
            self.base_archive_url = archive_url.split('#')[0].rstrip("/")
        key_to_hg = {}
        if known_hg_git_mapping is None:
            known_hg_git_mapping = {}

        for hg_log in hg_logs:
            node = hg_log["node"].strip()
            date = dateutil.parser.parse(hg_log["date"])
            self.hg_dates[node] = date
            self.hg_branches[node] = hg_log['branches'] if 'branches' in hg_log and hg_log['branches'] else None
            key = (date, hg_log["desc"].strip())
            key_to_hg.setdefault(key, []).append(node)
            if len(key_to_hg[key]) > 1:
                #logger.warning('duplicates "%s"\n %r', date, key_to_hg[key])
                pass
            self.hg_to_git[node] = None
            if node in known_hg_git_mapping:
                self.hg_to_git[node] = known_hg_git_mapping[node]
            self.hg_revnum_to_hg_node[int(hg_log["revnum"])] = node

        for git_log in git_logs:
            date = dateutil.parser.parse(git_log["date"])
            key = (date, git_log["desc"].strip())
            if key not in key_to_hg:
                #logger.warning('"%s" is not found in hg log', date)
                continue
            for node in key_to_hg[key]:
                # override duplicates by newest git hash
                self.hg_to_git[node] = git_log["node"].strip()

        for hg_node, git_node in self.hg_to_git.items():
            if git_node is None:
                print('BitBucket repo {}:Failed to find git hash for hg hash {}'.format(self.bb_repo, hg_node))

        self.sorted_nodes = sorted(self.hg_to_git)

    def find_hg_node(self, hg_node):
        idx = bisect.bisect_left(self.sorted_nodes, hg_node)
        if idx == len(self.sorted_nodes):
            return None
        full_node = self.sorted_nodes[idx]
        if full_node.startswith(hg_node):
            return full_node
        return None

    def hgnode_to_githash(self, hg_node):
        if hg_node in ("tip",):
            return None
        full_node = self.find_hg_node(hg_node)
        if full_node is None:
            if hg_node.isdigit() and int(hg_node) in self.hg_revnum_to_hg_node:
                hg_node = self.hg_revnum_to_hg_node[int(hg_node)]
                full_node = self.find_hg_node(hg_node)
        if full_node is None:
            #logger.warning("hg node %s is not found in hg log", hg_node)
            return None

        git_hash = self.hg_to_git.get(full_node, None)
        if git_hash is None:
            #logger.warning(
            #     'hg node %s "%s" is not found in git log',
            #     hg_node,
            #     self.hg_dates[full_node],
            # )
            return None

        return git_hash

    def convert_all(self, content):
        # make relative data links absolute
        urls = [
            'data/bitbucket.org/',
            'data/bytebucket.org/',
            'data/pf-emoji-service--cdn.us-east-1.prod.public.atl-paas.net/',
            'data/secure.gravatar.com/'
        ]
        if self.archive_url is not None:
            for url in urls:
                content = content.replace(url, '{}/{}'.format(self.base_archive_url, url))

        content = self.normalize_bb_url(content)
        content = self.convert_cset_marker(content)
        content = self.convert_bb_cset_link(content)
        content = self.convert_markupless_cset_marker(content)
        content = self.convert_bb_issue_link(content)
        content = self.convert_bb_src_link(content)
        content = self.convert_bb_user_link(content)
        content = self.convert_bb_pr_marker(content)
        content = self.replace_bb_url_with_archive(content)
        return content

    def convert_other_repo_content(self, content):
        content = self.normalize_bb_url(content)
        content = self.convert_bb_cset_link(content, git_repo_prefix=True)
        content = self.convert_markupless_cset_marker(content, git_repo_prefix=True)
        content = self.convert_bb_issue_link(content, git_repo_prefix=True)
        content = self.convert_bb_src_link(content)
        content = self.replace_bb_url_with_archive(content)
        return content

    def convert_cset_marker(self, content, git_repo_prefix=False):
        r"""
        before-1: '<<cset 0f18c81b53fc>>'  (hg-node)
        before-2: '<<changeset 0f18c81b53fc>>'  (hg-node)
        before-3: '<<changeset 123:0f18c81b53fc>>'  (hg-node)
        before-4: '<<changeset 123>>'  (hg-node)
        after: '\<\<cset 20fa9c09b23e\>\>'  (git-hash)
        """
        captures = re.findall(r"<<(cset|changeset) ([^>]+)>>", content)
        for marker, hg_node in captures:
            if ":" in hg_node:  # for '718:714c805d842f'
                git_hash = self.hgnode_to_githash(hg_node.split(":")[1])
            else:
                git_hash = self.hgnode_to_githash(hg_node)

            # only replace the hash if we actually found it
            if git_hash is not None:
                if git_repo_prefix:
                    git_hash = self.gh_repo + '@' + git_hash
                content = content.replace(
                    r"<<%s %s>>" % (marker, hg_node), r"\<\<cset %s\>\>" % git_hash
                )
        return content

    def convert_markupless_cset_marker(self, content, git_repo_prefix=False):
        "finds floating cset fragments and converts them to a githash (prepended with the github repo if requested)"

        def repl(matchobj):
            first = matchobj.group(1)
            second = matchobj.group(2)
            third = matchobj.group(3)
            hg_node = matchobj.group(4)

            # if we have matched a hg_node outside of a []() URL
            if hg_node is not None:
                git_hash = self.hgnode_to_githash(hg_node)
                # only replace the URL if the hash was found in this repo
                if git_hash is not None:
                    repo = ""
                    if git_repo_prefix:
                        repo = self.gh_repo + "@"
                    return repo+git_hash
                else:
                    return matchobj.group(0)
            # else we ignore it. If the URL is pointing to a changeset, then it will be picked up by 
            # convert_bb_cset_link. If the URL is not pointing to a changeset, but the link text still contains a hash...
            # well, that's tricky to handle in a way everyone would be happy with so we'll just ignore it.
            else:
                return matchobj.group(0)

        content = re.sub(r"""(                          # match the []() URL format
                                (?:
                                    (?<!\\)\[           # match an opening square bracket not preceeded by a slash
                                )
                                |
                                (?:
                                    (?<!\\\\\\)(?<=\\\\)\[ # or an opening square brack preceeded by two slashes but not three
                                )
                            )
                            (
                                (?:
                                    (?:[^\]\[])         # match anything but an open or close square bracket
                                    |
                                    (?:                 # or a close square bracket preceeded by one slash but not two
                                        (?<!\\\\)
                                        (?<=\\)
                                        \]
                                    )
                                    |
                                    (?:                 # or an open bracket preceeded by one slash but not two
                                        (?<!\\\\)
                                        (?<=\\)
                                        \[
                                    )
                                    |
                                    (?:                 # or an opening square brack preceeded by three slashes
                                        (?<=\\\\\\)\[ 
                                    )
                                    |
                                    (?:                 # or an close square brack preceeded by three slashes
                                        (?<=\\\\\\)\] 
                                    )
                                )*                      # repeat zero or more times
                            )
                            (
                                (?:                     # followed by a closing bracket not preceeded by a single slash
                                    (?<!\\)
                                    (?:\]\(.*?\))
                                )
                                |
                                (?:                     # or a closing bracket preceeded by two but not three slashes
                                    (?<!\\\\\\)
                                    (?<=\\\\)
                                    (?:\]\(.*?\))
                                )
                            )
                            |
                            (?:                         # match the main regex we care about if it is not within a []() URL
                                (?<!/)                  # and is not preceeded by a "/" which would indicate it was part of a URL
                                (\b[0-9a-f]{7,40}\b)    # match a hex has between length 7 and 40
                            )""", repl, content, flags=re.MULTILINE|re.VERBOSE)

        return content

    def normalize_bb_url(self, content):
        # convert back the relative links to archive template
        # (this will be returned to the non-relative archive link in replace_bb_url_with_archive())
        content = content.replace("#!/{}/".format(self.bb_repo), 'https://bitbucket.org/{}/'.format(self.bb_repo))
        if self.archive_url is not None:
            # convert relative data links to the archive URL
            content = content.replace("data/repositories/{}/".format(self.bb_repo), '{}/data/repositories/{}/'.format(self.base_archive_url, self.bb_repo))

        content = content.replace("http://www.bitbucket.org/", "https://bitbucket.org/")
        content = content.replace("http://bitbucket.org/", "https://bitbucket.org/")
        content = content.replace(
            "{0.bb_url}/changeset/".format(self), "{0.bb_url}/commits/".format(self)
        )
        return content

    def replace_bb_url_with_archive(self, content):
        if self.archive_url is not None:
            content = content.replace(self.bb_url, self.archive_url)
        return content

    # TODO: This will probably fail badly if the matching URL is in the link text part of the []() formatted URL
    def convert_bb_cset_link(self, content, git_repo_prefix=False):
        r"""
        before: bb_url + '/commits/e282b3a8ef4802da3a685f10b5e9a39633e2c23a'
        after:
            it matches whether it is a bare URL or a []() formatted URL
            if there is an archive URL:
                it just replaces the bb url with the archive url
                if it is a bare URL:
                    appends a cset hash after the link in brackets (using git_repo_prefix if appropriate)
                if it is a []() formatted URL
                    appends a cset hash in brackets (outside of the []() and using git_repo_prefix if appropriate)
            if there is not an archive URL:
                if it is a bare URL:
                    replace with cset hash (using git_repo_prefix if appropriate)
                if it is a []() formatted URL:
                    replace the URL with a github cset link but do not modify the text
        """
        
        def repl(matchobj):
            first = matchobj.group(1)
            url = matchobj.group(2)
            branch = matchobj.group(3)
            hg_node = matchobj.group(4)
            rest_of_url = matchobj.group(5)
            last = matchobj.group(6)

            # make sure none of them are None
            first = '' if first is None else first
            url = '' if url is None else url
            branch = '' if branch is None else branch
            hg_node = '' if hg_node is None else hg_node
            rest_of_url = '' if rest_of_url is None else rest_of_url
            last = '' if last is None else last

            formatted_url = False
            if len(first) == 3 and first[-2:] == '](' and first[0] != '\\':
                if last == ')':
                    formatted_url = True
                else: 
                    # we've found a URL in the () portion of a []() markdown formatted URL, but it doesn't conform
                    # to the expected format, so we will skip it
                    return  matchobj.group(0)

            # TODO: write this to handle rewriting branch URLs
            if branch == 'branch/':
                return matchobj.group(0)

            git_hash = self.hgnode_to_githash(hg_node)
            # only replace the URL if the hash was found in this repo
            if git_hash is not None:
                if self.archive_url is not None:
                    to_ = first + self.archive_url + '/commits/' + hg_node + rest_of_url 
                    # If it's a formatted []() url, then add the github link after the entire match
                    if formatted_url:
                        to_ += last

                    repo = ""
                    if git_repo_prefix:
                        repo = self.gh_repo + "@"
                    to_ += " ({repo}{hash})".format(repo=repo, hash=git_hash)

                    # if it's not a formatted url, make sure we don't throw away the matched content that came afterwards!
                    if not formatted_url:
                        to_ += last
                else:
                    if formatted_url:
                        to_ = first + self.gh_url + '/commit/' + git_hash + last
                    else:
                        repo = ""
                        if git_repo_prefix:
                            repo = self.gh_repo + "@"
                        to_ = first + "{repo}{hash}".format(repo=repo, hash=git_hash) + last
            else:
                # Can't find the hg hash, so we skip it
                return matchobj.group(0)

            return to_
                
        base_url = self.bb_url + "/commits/"
        content = re.sub(r"(.{3})?(" + re.escape(base_url) + r")(?:(branch/)?)([0-9a-f]+)?((?:\?(?:[^\)])*)|(?:/)?)(.{1})?", repl, content, flags=re.MULTILINE)
        # content = re.sub(r"(.{3})?(" + re.escape(base_url) + ")([0-9a-f]+)(/?)([^\(]*?[\)])?", repl, content, flags=re.MULTILINE)
        return content

    # 
    # Dev notes:
    #
    # Test str (we want to match against every #1xx PR but not every #2xx issue in the test str):
    #   blah blah pull request #17 blah blah [pull request #23](asdf) blah blah [labscript pull request #25]() hjkhaskdjhdshdsa kjdhask pull request #11 hjksdahjadsh [labscript blah blah ] pull request #15 ]() pull request #14 [labscript blah blah \] pull request #28 ]() pull request #113 pull request #167  [labscript blah blah pull request #28 \] ]() [labscript blah blah pull request #15] ]() [labscript blah blah \] pull request #28 \] ]()
    #
    # Almost works:
    #   (\[.*?(\b(pull request #(\d+))\b)?.*?(?<!\\)\])*.*?(\b(pull request #(\d+))\b)?
    # I think this works: 
    #   ((\[.*?(\b(pull request #(\d+))\b)?.*?(?<!\\)\])|(\b(pull request #(\d+))\b))
    # Cleaned up a little:
    #   (?:\[.*?(\bpull request #\d+\b)?.*?(?<!\\)\])|(?:\b(pull request #(\d+))\b)
    # Better:
    #   (?:(?<!\\)\[(?:[^\]]*?(?:\\\][^\]]*?)?)?(\bpull request #\d+\b)?(?:[^\]]*?(?:\\\][^\]]*?)?)?(?<!\\)\]\(.*?\))|(?:\b(pull request #(\d+))\b)
    #
    #   Now any match with a non-empty group 2/3 is a match we want to replace!
    #
    #
    #
    # TODO: This falls over for some obscure cases like: "[labscript blah blah pull request #15\\] ]()" which should match the malformed markdown but doesn't.
    def convert_bb_pr_marker(self, content):
        r"""
        Matches pull request text ("pull request #???" or "PR #???") and replaces it with a markdown link to either the BitBucket archive or BitBucket itself
        The complex regex ensures that we do not replace matching text that is alredy inside a valid markdown link.
        This is necessary for content that has links to pull requests in other repositories, which may be formatted similarly, e.g.
            [other repository pull request #3](url to repo pull request)
        and should not re reformatted by this method.
        The replace_bb_url_with_archive() method will catch these cases later (and rewrite them to the BitBucket archive URL if it exists)
        """

        if self.archive_url is not None:
            url = "%s/pull-requests" % (self.archive_url)
        else:
            url = "%s/pull-requests" % (self.bb_url)

        def repl(matchobj):
            if matchobj.group(2) is not None:
                return "[{all}]({url}/{id})".format(all=matchobj.group(0), url=url, id=matchobj.group(3))
            else:
                return matchobj.group(0)

        content = re.sub(
            r'(?:(?<!\\)\[(?:[^\]]*?(?:\\\][^\]]*?)?)?(\bpull request #\d+\b)?(?:[^\]]*?(?:\\\][^\]]*?)?)?(?<!\\)\]\(.*?\))|(?:\b(pull request #(\d+))\b)',
            repl,
            content,
            flags=re.MULTILINE|re.IGNORECASE
        )

        content = re.sub(
            r'(?:(?<!\\)\[(?:[^\]]*?(?:\\\][^\]]*?)?)?(\bPR #\d+\b)?(?:[^\]]*?(?:\\\][^\]]*?)?)?(?<!\\)\]\(.*?\))|(?:\b(PR #(\d+))\b)',
            repl,
            content,
            flags=re.MULTILINE|re.IGNORECASE
        )
        return content

    def convert_bb_src_link(self, content):
        r"""
        before: bb_url + '/src/e2a0e4fde89998ed46198291457d2a822bc60125/path/to/file.py?at=default#cl-321'
                (note, the "cl-" line prefix is actually the old format. It's now "lines-" instead.
                However, BitBucket actually accepts anything or nothing as the prefix, presumably
                so that BitBucket is backwards compatible with the old format, so we'll do the same)
        after: gh_url + '/blob/6336eab7c825852a058ed8a744be905c003ccbb8/path/to/file.py#L321'
        """
        base_url = self.bb_url + "/src/"
        url_pairs = re.findall(base_url + r"([^/]+)(/[\w\d/?&=#.,_-]*)?", content)
        for hg_node, rest_of_url in url_pairs:
            parsed_url = urlparse.urlparse(rest_of_url)
            line = ""
            if re.search("-\d+", parsed_url.fragment):
                line = "#L" + re.search("-(\d+)", parsed_url.fragment).groups()[0]
            git_hash = self.hgnode_to_githash(hg_node)
            if git_hash is None:
                git_hash = "master"
            from_ = base_url + hg_node + rest_of_url
            to_ = self.gh_url + "/blob/%s%s%s" % (git_hash, parsed_url.path, line)
            content = content.replace(from_, to_)
            #logging.info("%s -> %s", from_, to_)
        return content

    # TODO: This will probably fail badly if the matching URL is in the link text part of the []() formatted URL
    def convert_bb_issue_link(self, content, git_repo_prefix=False):
        r"""
        before: bb_url + '/issue/63/issue-title-string'
        after:
            it matches whether it is a bare URL or a []() formatted URL
            if there is an archive URL:
                it just replaces the bb url with the archive url
                if it is a bare URL:
                    appends a github issue after the link in brackets (using git_repo_prefix if appropriate)
                if it is a []() formatted URL
                    appends a github issue in brackets (outside of the []() and using git_repo_prefix if appropriate)
            if there is not an archive URL:
                if it is a bare URL:
                    replace with github issue (using git_repo_prefix if appropriate)
                if it is a []() formatted URL:
                    replace the URL with a github issue link but do not modify the text
        """

        def repl(matchobj):
            first = matchobj.group(1)
            url = matchobj.group(2)
            issue_num = matchobj.group(3)
            rest_of_url = matchobj.group(4)
            last = matchobj.group(5)

            # make sure none of them are None
            first = '' if first is None else first
            url = '' if url is None else url
            issue_num = '' if issue_num is None else issue_num
            rest_of_url = '' if rest_of_url is None else rest_of_url
            last = '' if last is None else last

            formatted_url = False
            if len(first) == 3 and first[-2:] == '](' and first[0] != '\\':
                if last == ')':
                    formatted_url = True
                else: 
                    # we've found a URL in the () portion of a []() markdown formatted URL, but it doesn't conform
                    # to the expected format, so we will skip it
                    return  matchobj.group(0)

            if self.archive_url is not None:
                to_ = first + self.archive_url + '/issues/' + issue_num + rest_of_url 
                # If it's a formatted []() url, then add the github link after the entire match
                if formatted_url:
                    to_ += last

                repo = ""
                if git_repo_prefix:
                    repo = self.gh_repo
                to_ += " ({repo}#{id})".format(repo=repo, id=issue_num)

                # if it's not a formatted url, make sure we don't throw away the matched content that came afterwards!
                if not formatted_url:
                    to_ += last
            else:
                if formatted_url:
                    to_ = first + self.gh_url + '/issues/' + issue_num + last
                else:
                    repo = ""
                    if git_repo_prefix:
                        repo = self.gh_repo
                    to_ = first + "{repo}#{id}".format(repo=repo, id=issue_num) + last

            return to_ 
                
        base_urls = [
            self.bb_url + "/issue/",
            self.bb_url + "/issues/"
        ]
        for base_url in base_urls:
            content = re.sub(r"(.{3})?(" + re.escape(base_url) + r")(\d+)(/[\w\d.,_-]*)?(.{1})?", repl, content, flags=re.MULTILINE)
            
        return content

    def convert_bb_user_link(self, content):
        # TODO: make this more robust as the get_bb_username has special cases in it
        r"""
        before: @{UUID} or @{account-id}
        after: '[@username](profile_url)' or @github-username
        """
        # base_url = self.bb_url
        base_url = "https://bitbucket.org/"
        #(^|[\n ^a-zA-Z0-9])@([\{a-zA-Z])([a-zA-Z0-9\:\}_\-\}]+)
        # pattern = r"(^|[^a-zA-Z0-9])@([a-zA-Z][a-zA-Z0-9_-]+)\b"
        for bbname, ghname in self.user_mapping.items():
            content = content.replace("@{} ".format(bbname), "@{} ".format(ghname))
        pattern = r"\@\{(.*?)\}"
        for user_id in re.findall(pattern, content):
            user = get_bb_username(user_id)
            if user is not None:
                if user['nickname'] in self.user_mapping:
                    name = self.user_mapping[user['nickname']]
                    content = content.replace("@{"+user_id+"}", "@"+name)
                else:
                    content = content.replace("@{"+user_id+"}", "[{display_name} ({nickname})]({links[html][href]})".format(**user))
        return content


def get_git_log(repo_path):
    uuid_item_delim = "|{}|".format(str(uuid.uuid4()))
    uuid_node_delim = "|{}|".format(str(uuid.uuid4()))

    cmd = [
        "git",
        "log",
        "--all",
        "--date-order",
        "--reverse",
        "--pretty=format:{node}%H{item}%ad{item}%ae{item}%B".format(
            node=uuid_node_delim, item=uuid_item_delim
        ),
    ]
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        cwd=repo_path,
        universal_newlines=True,
        errors="replace",
    )
    data, _ = p.communicate()
    splittable_data = '\n' + data

    output = []
    for i, d in enumerate(splittable_data.split("\n" + uuid_node_delim)):
        if not d:
            continue
        message = {}
        message["node"], message["date"], message["email"], message["desc"] = d.split(
            uuid_item_delim
        )
        message["revnum"] = message["node"]
        message["desc"] = message["desc"].rstrip("\n").encode('ascii', 'replace').decode()
        output.append(message)

    return output

def get_hg_hashes_from_git(repo_path):
    uuid_item_delim = "|{}|".format(str(uuid.uuid4()))
    uuid_node_delim = "|{}|".format(str(uuid.uuid4()))

    cmd = [
        'git', 
        'log', 
        '--branches', 
        '--show-notes=hg',
        "--date-order",
        "--reverse",
        "--pretty=format:{node}%H{item}%N".format(
            node=uuid_node_delim, item=uuid_item_delim
        ),
    ]
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        cwd=repo_path,
        universal_newlines=True,
        errors="replace",
    )
    data, _ = p.communicate()
    splittable_data = '\n' + data

    output = {}
    for i, d in enumerate(splittable_data.split("\n" + uuid_node_delim)):
        if not d:
            continue
        message = {}
        message["git_node"], message["hg_node"] = d.split(
            uuid_item_delim
        )
        message["hg_node"] = message["hg_node"].rstrip("\n")
        output[message["hg_node"]] = message["git_node"]

    return output

def get_hg_log(repo_path):
    uuid_node_delim = "|{}|".format(str(uuid.uuid4()))
    uuid_item_delim = "|{}|".format(str(uuid.uuid4()))
    templates = [
        "{rev}|{node}|{date|isodatesec}\n",
        "{desc}" + uuid_item_delim + "{author}" + uuid_item_delim + "{branches}" + uuid_node_delim,
    ]


    hg_data = []
    for t in templates:
        cmd = ["hg", "log", "-R", repo_path, "--template", t, "-v", "-y", "-q"]
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, universal_newlines=True, errors="replace"
        )
        data, _ = p.communicate()
        if uuid_node_delim in t:
            hg_data.append(data.split(uuid_node_delim))
        else:
            hg_data.append(data.split("\n"))

    output = []
    for i, d in enumerate(hg_data[0]):
        if not d:
            continue
        message = {}
        message["revnum"], message["node"], message["date"] = d.split("|")
        message["desc"], message['email'], message['branches'] = hg_data[1][i].split(uuid_item_delim)
        message["desc"]  = message["desc"].rstrip("\n").encode('ascii', 'replace').decode()
        message["email"] = message["email"].replace("\r", "")
        output.append(message)

    return list(reversed(output))


if __name__ == "__main__":
    import pprint
    import sys

    ftype = sys.argv[1]
    repo_path = sys.argv[2]
    if ftype == "hg":
        r = get_hg_log(repo_path)
        pprint.pprint(r, indent=4)
    elif ftype == "git":
        r = get_git_log(repo_path)
        pprint.pprint(r, indent=4)
    elif ftype == "compare":
        git_repo_path = sys.argv[3]
        rhg = get_hg_log(repo_path)
        rgit = get_git_log(git_repo_path)
        print(
            "Below are the items which did not match, likely due to git vs hg reordering"
        )

        mapping = BbToGh(rhg, rgit, '', '', '', '')

        for h, g in zip(rhg, rgit):
            print(h['revnum'], h['branches'])
            if mapping.hgnode_to_githash(h['node']) is None:
                print(h)
            # if h["desc"] != g["desc"]:
            #     print('--')
            #     print(h["revnum"])
            #     print(h["desc"])
            #     print('--')
            #     print(g["desc"])

