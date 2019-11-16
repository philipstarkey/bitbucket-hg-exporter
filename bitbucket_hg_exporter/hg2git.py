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

# TODO: Update code to replace links with links to the archived site as the BitBucket content will be deleted!


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


# TODO: Fix this or repace it
@memoize()
def get_bb_username(user):
    # fmt: off
    if user in ('name', 'names', 'class', 'import', 'property', 'ubuntu', 'wrap',
                'github', 'for', 'enumerate', 'item', 'itemize', 'type', 'title',
                'empty', 'replace', 'gmail', 'id', 'href', 'app', 'echo'):
        #logging.info('user @%s is skipped. It\'s a some code.', user)
        return False
    # fmt: on
    base_user_api_url = "https://bitbucket.org/api/1.0/users/"
    res = requests.get(base_user_api_url + user)
    if res.status_code == 200:
        #logging.debug("user @%s is exist in BB.", user)
        return res.json()["user"]["display_name"]
    else:
        #logging.debug("user @%s is not found in BB.", user)
        return None


class BbToGh(object):
    """
    Convert BB links and changeset markers in the issues.json
    * Normalize BB old URLs.
    * Convert BB changeset marker into GH.
    * Convert BB changeset links into GH.
    * Convert BB issue links into GH.
    * Convert BB src links into GH.
    """

    def __init__(self, hg_logs, git_logs, bb_url, gh_url):
        self.bb_url = bb_url.rstrip("/")
        self.gh_url = gh_url.rstrip("/")
        self.hg_to_git = {}
        self.hg_dates = {}
        self.hg_revnum_to_hg_node = {}
        key_to_hg = {}

        for hg_log in hg_logs:
            node = hg_log["node"].strip()
            date = dateutil.parser.parse(hg_log["date"])
            self.hg_dates[node] = date
            key = (date, hg_log["desc"].strip())
            key_to_hg.setdefault(key, []).append(node)
            if len(key_to_hg[key]) > 1:
                #logger.warning('duplicates "%s"\n %r', date, key_to_hg[key])
                pass
            self.hg_to_git[node] = None
            self.hg_revnum_to_hg_node[hg_log["revnum"]] = node

        for git_log in git_logs:
            date = dateutil.parser.parse(git_log["date"])
            key = (date, git_log["desc"].strip())
            if key not in key_to_hg:
                #logger.warning('"%s" is not found in hg log', date)
                continue
            for node in key_to_hg[key]:
                # override duplicates by newest git hash
                self.hg_to_git[node] = git_log["node"].strip()

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
            if hg_node.isdigit():
                hg_node = self.hg_revnum_to_hg_node[int(hg_node)]
                full_node = self.find_hg_node(hg_node)
                if full_node is None:
                    #logger.warning("hg node %s is not found in hg log", hg_node)
                    return None
        git_hash = self.hg_to_git[full_node]
        if git_hash is None:
            #logger.warning(
            #     'hg node %s "%s" is not found in git log',
            #     hg_node,
            #     self.hg_dates[full_node],
            # )
            return None

        return git_hash

    def convert_all(self, content):
        content = self.normalize_bb_url(content)
        content = self.convert_cset_marker(content)
        content = self.convert_bb_cset_link(content)
        content = self.convert_bb_issue_link(content)
        content = self.convert_bb_src_link(content)
        content = self.convert_bb_user_link(content)
        content = self.convert_bb_pr_marker(content)
        return content

    def convert_cset_marker(self, content):
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
            content = content.replace(
                r"<<%s %s>>" % (marker, hg_node), r"\<\<cset %s\>\>" % git_hash
            )
        return content

    def normalize_bb_url(self, content):
        content = content.replace("http://www.bitbucket.org/", "https://bitbucket.org/")
        content = content.replace("http://bitbucket.org/", "https://bitbucket.org/")
        content = content.replace(
            "{0.bb_url}/changeset/".format(self), "{0.bb_url}/commits/".format(self)
        )
        return content

    def convert_bb_cset_link(self, content):
        r"""
        before: bb_url + '/commits/e282b3a8ef4802da3a685f10b5e9a39633e2c23a'
        after: ' 1d063726ee185dce974f919f2ae696bd1b6b826b '
        """
        base_url = self.bb_url + "/commits/"
        url_pairs = re.findall(base_url + r"([0-9a-f]+)(/?)", content)
        for hg_node, rest_of_url in url_pairs:
            git_hash = self.hgnode_to_githash(hg_node)
            from_ = base_url + hg_node + rest_of_url
            to_ = " %s " % git_hash
            content = content.replace(from_, to_)
            #logging.info("%s -> %s", from_, to_)
        return content

    def convert_bb_pr_marker(self, content):
        r"""
        before: 'pull request #123'
        after: self.bb_url + '/pull-request/123'
        """
        captures = re.findall(r"\b(pull request #(\d+))\b", content)
        for replacer, pr_number in captures:
            content = content.replace(
                replacer, "%s/pull-request/%s" % (self.bb_url, pr_number)
            )
        return content

    def convert_bb_src_link(self, content):
        r"""
        before: bb_url + '/src/e2a0e4fde89998ed46198291457d2a822bc60125/path/to/file.py?at=default#cl-321'
        after: gh_url + '/blob/6336eab7c825852a058ed8a744be905c003ccbb8/path/to/file.py#L321'
        """
        base_url = self.bb_url + "/src/"
        url_pairs = re.findall(base_url + r"([^/]+)(/[\w\d/?=#.,_-]*)?", content)
        for hg_node, rest_of_url in url_pairs:
            parsed_url = urlparse.urlparse(rest_of_url)
            line = ""
            if re.match("cl-\d+", parsed_url.fragment):
                line = "#L" + re.match("cl-(\d+)", parsed_url.fragment).groups()[0]
            git_hash = self.hgnode_to_githash(hg_node)
            if git_hash is None:
                git_hash = "master"
            from_ = base_url + hg_node + rest_of_url
            to_ = self.gh_url + "/blob/%s%s%s" % (git_hash, parsed_url.path, line)
            content = content.replace(from_, to_)
            #logging.info("%s -> %s", from_, to_)
        return content

    def convert_bb_issue_link(self, content):
        r"""
        before: bb_url + '/issue/63/issue-title-string'
        after: '#63'
        """
        base_url = self.bb_url + "/issue/"
        issue_pairs = re.findall(base_url + r"(\d+)(/[\w\d.,_-]*)?", content)
        for issue_id, rest_of_url in issue_pairs:
            from_ = base_url + issue_id + rest_of_url
            to_ = "#%s" % issue_id
            content = content.replace(from_, to_)
            #logging.info("%s -> %s", from_, to_)
        return content

    def convert_bb_user_link(self, content):
        # TODO: make this more robust as the get_bb_username has special cases in it
        r"""
        before: '@username'
        after: '[@username](https://bitbucket.org/username)'
        """
        # base_url = self.bb_url
        base_url = "https://bitbucket.org/"
        pattern = r"(^|[^a-zA-Z0-9])@([a-zA-Z][a-zA-Z0-9_-]+)\b"
        for prefix, user in re.findall(pattern, content):
            name = get_bb_username(user)
            if name is not None:
                content = re.sub(
                    pattern, r"\1[%s](%s)" % (name, base_url + user), content
                )
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

    output = []
    for i, d in enumerate(data.split("\n" + uuid_node_delim)):
        if not d:
            continue
        message = {}
        message["node"], message["date"], message["email"], message["desc"] = d.split(
            uuid_item_delim
        )
        message["revnum"] = message["node"]
        message["desc"] = message["desc"].rstrip("\n")
        output.append(message)

    return output


def get_hg_log(repo_path):
    uuid_delim = "|{}|".format(str(uuid.uuid4()))
    templates = [
        "{rev}|{node}|{date|isodatesec}\n",
        "{author}\n",
        "{desc}" + uuid_delim,
    ]

    hg_data = []
    for t in templates:
        cmd = ["hg", "log", "-R", repo_path, "--template", t, "-v", "-y", "-q"]
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, universal_newlines=True, errors="replace"
        )
        data, _ = p.communicate()
        if uuid_delim in t:
            hg_data.append(data.split(uuid_delim))
        else:
            hg_data.append(data.split("\n"))

    output = []
    for i, d in enumerate(hg_data[0]):
        if not d:
            continue
        message = {}
        message["revnum"], message["node"], message["date"] = d.split("|")
        message["email"] = hg_data[1][i].replace("\r", "")
        message["desc"] = hg_data[2][i].rstrip("\n")
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
        for h, g in zip(rhg, rgit):
            if h["desc"] != g["desc"]:
                print(h["revnum"])
                print(h["desc"])
                print(g["desc"])

