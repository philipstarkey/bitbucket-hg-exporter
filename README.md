# bitbucket-hg-exporter

This tool provides a command line interface for exporting all project data from a BitBucket mercurial repository. This includes pull request history, issue trackers, wikis, forks, attachments and commit comments. This data (where possible) can optionally be migrated to GitHub. An archive of all BitBucket data can be optionally published in a GitHub pages repository so that a HTML record of your BitBucket repositories (and associated pull-requests, issues and comments) can be preserved.

Issues can also be imported to GitHub, and commit hashes, issues links, pull-request links, URLs (including to source code), usernames, etc. will be rewritten to point to GitHub and/or the generated BitBucket archive depending on the availability of the content (for example, pull-requests are only in the BitBucket archive). This process can also handle inter-repository links, converting them to the appropriate GitHub style (which is very nice since BitBucket never supported this properly!). 

You can see a complex example of such a migration [here](https://github.com/labscript-suite) (with forks archived [here](https://github.com/labscript-suite-archive)).

## How does it work?
We recursively download all JSON files (and binary attachments) from the BitBucket API for each repository to be backed up. URLs within these JSON files are rewritten to be relative to other downloaded JSON files. We have a single-page application (written in AngularJS) that reads these JSON files and recreates a (currently very poor) approximation of the BitBucket repository. In order to save space, we do not download diffs or source files from the BitBucket API (although we do locally clone the hg repo which contains the equivalent information). Instead, diffs and source files are linked to the migrated repository hosted on GitHub (should you choose to migrate to GitHub).

## Why does this exist?
Because BitBucket have decided to just delete all mercurial repositories at the end of May 2020. This is disastrous, and discussions within projects will be lost because BitBucket are not providing any export or migration tools. Even with this tool, I expect a lot of history will be lost because this tool relies on people to migrate their projects (and many projects on BitBucket are not actively maintained).

Quite frankly, BitBucket shouldn't be deleting user data (especially is those users have been paying). They should be at least leaving a read only copy online. **But there is no indication they will do this**. If I was an Atlassian Git customer, I would be very, very concerned about the longevity of BitBucket cloud given this attitude.

## Current project status
Under active development. Beta stage, but very useable.

The import to GitHub is probably as good as it's going to get (alternatively, you can now use a local conversion tool and then push to GitHub for a much better experience).
Wikis need to be migrated by hand (see below).
If using the GitHub source importer (rather than a local conversion tool), then author attribution also needs to be done via the GitHub web interface (prior to pushing any new commits to GitHub) as the GitHub API for this doesn't seem to correctly link user/email combinations to GitHub accounts.

There may be subsequent improvements to the HTML archive of BitBucket content (that can be published to GitHub pages). However, because this just reads the BitBucket API JSON files which are already downloaded locally to your PC (and possibly published on GitHub pages), you can migrate to GitHub now and then apply the new template to your archive to gain the improvements once they are available.

## How can I thank you?
If you are a business or enterprise, and my tool saved you money, please consider donating 10% of the saved money to me to show your thanks. You can donate to me via PayPal [here](https://www.paypal.me/philiptstarkey). I'd also encourage you to donate the remaining 90% to other open source projects you use.

If you are an individual, non-profit, or using this tool for an open source project, then I'd appreciate it if you could "star" my project and share it with other people who would benefit from the tool. You're also welcome to contribute via the PayPal link above if you would like to show your thanks that way.

## Installation
You need to install Python 3.7+ (or a python virtual environment with Python 3.7+). I recommend [Anaconda Python](https://www.anaconda.com/distribution/#download-section) if you are new to Python.

You also need to make sure mercurial (`hg`) and `git` executables are available in your path. If you're unsure, open a terminal and type those commands to see if they are available. If they are not, install them. Note, git is strictly only needed if you are importing to GitHub.

Then from your terminal (or Anaconda Prompt on windows if using Anaconda Python) run: `pip install bitbucket_hg_exporter`

To upgrade to new versions, run: `pip install -U bitbucket_hg_exporter`

## How to use
Once installed, an executable is inserted into your OS PATH (provided the relevant Python environment is activated). You can thus launch this tool from the terminal (or Anaconda prompt on windows if using Anaconda Python) by running: 

`bitbucket-hg-exporter` 

and following the onscreen prompts.

There are also a couple of command line options that can help speed things up. I recommend only using them once you've got a hang of using the tool, and if you need to use the tool a lot. They are:

1. `--load` which skips the "start new project"/"load project" question and goes straight to load project.
2. `--storage-dir <path to storage directory>` which skips the question asking for the location of your saved project and uses the path specified. Requires the use of `--load`
3. `--project-name <your project name>` which skips the project selection and uses the saved project specified. This requires the use of `--load` and `--storage-dir` and the project must exist inside the specified storage directory.

## FAQ
### How is the mercurial repository converted to git?
This is done either by GitHub, or by a separate tool of your choice (I suggest [hg-export-tool](https://github.com/chrisjbillington/hg-export-tool) which wraps hg-fast-export but handles some corner cases it does not). Note that branches with multiple heads (and other similar corner cases) are not well handled by the GitHub source importer (in which case you should use a local conversion tool). If you are unsure, do a test run using the GitHub source importer. My tool will write out a file of missing commit hashes just before importing issues to GitHub. If missing commits are identified, use a local tool to import. Note: I can't guarantee I'll detect lost commits - always verify all your commits are there regardless of whether you use GitHub or a local tool to convert to git.

### Does this tool leak any private data?
Because we're downloading the BitBucket API for your repository using credentials you supply, anything those credentials can see will be made public if you publish it to GitHub.
This may include things you thought were deleted (like comments). If this concerns you, consider using the tool to just create a local backup. You can then review the content and publish it by hand (or to a private website).

As this tool is only in the alpha stage of development (and will likely stay like that for the entire life of the project) there may be other bugs that leak private data. Hopefully there are not, but you use this at your own risk. This tool is primarily aimed at open source projects where there is limited private data to leak, none of which should be in the mercurial repository or accessible through the (authenticated) bitbucket API. If you do have private data which is accessible through one of those mechanisms, you should explicitly check that this tool will not make it publicly accessible and take any steps necessary to protect your data.

### Does this tool do everything for me or do I have to do some things by hand?
Unfortunately, there is no easy programmatic way to initialise the GitHub wiki repository. So while this tool saves a copy of your BitBucket wiki repos locally on your PC, it does not publish it to GitHub.

However, I have devised a procedure to do this by hand, which is as follows:

1. Use this tool to import the master repository (the BitBucket code repo that the wiki is attached to) to GitHub.
2. Enable the GitHub wiki on this new repo, **and create a single blank page**. This step is needed to initialise the wiki's git repo. This is what I'll call the "actual wiki" for the purposes of these instructions.
3. Use the GitHub source import tool to import the hg wiki to a new github repository. Give it the URL `<bitbucket repo>/wiki` (which is what you would use to clone the repo locally). We'll call this "temp wiki" for the purposes of these instructions.
4. Clone the temp wiki to your PC.
5. Add the actual (currently unpopulated) wiki as a git remote: `git remote add wiki https://github.com/<owner>/<repo>.wiki.git`
6. Pull from the new git remote into your temp wiki local clone: `git pull wiki master --strategy-option ours --allow-unrelated-histories`
This is a bit of a hack, but allows you to merge two unrelated git repos (effectively)
7. Push the merged repo back to the actual wiki: `git push wiki master`

This tool also doesn't yet save downloads. The JSON files from the BitBucket API and the associated downloads are not going to be saved or archived. GitHub will use tags as releases though, and create new tar.gz/zip bundles for each tag and list them under releases. If you have "downloads" under BitBucket, you should check to see what is missing after import.

### Does this tool handle author attributions?
You can specify a mapping between BitBucket and GitHub accounts. This will be used in the BitBucket archive template and also any issues imported to GitHub. 

**It is not used for commit attribution in the migrated GitHub repository**. You will need to do this by hand, prior to pushing any new commits to the repository. If converting using GitHub, GitHub will email you the URL you can use to specify this mapping (through the GitHub web interface) but it will be `<github repo url>/import/authors`. If you use a local tool, refer to it's documentation for how to specify author mapping.

### I have a large number of repositories to backup. How do I work around the BitBucket API rate limit?
This tool allows you to specify multiple BitBucket accounts in order to work around the tiny API rate limit that Atlassian impose.
However, it's only useful to do this if you have multiple repositories you are backing up.
The list of repositories is split amongst the accounts provided in order to not split the API download for a given repository across multiple accounts (which could result in discontinuities between files or URLs containing a "context" failing because that context is assigned to another user).
Please make sure that all of your accounts have the same access permissions to the repositories as the primary account you give to this tool.

### I don't want to import to GitHub, do I need to?
Nope. This tool is quite happy just downloading everything locally and you can do what you want with it. It will even generate the HTML archive for you to publish somewhere else if that's what you want to do.

### I have multiple repositories that are related to each other. How does your tool handle that?
Links between repositories exported/imported as part of the same project (the bitbucket_hg_exporter tool project, not BitBucket project) will have inter-repository links rewritten appropriately. You can also specify an additional set of URL rewrites in a JSON file if you need to rewrite other URLs too.

### I've already migrated my repository to GitHub, can I still use this tool?
Yes, you can still generate the BitBucket archive pages and link to your existing GitHub project provided you have not deleted the repository from BitBucket. The command line wizard will prompt you to specify the GitHub URL(s) for any repositories already migrated. You might not want to import the issues to GitHub if you've already had new issues or pull-requests made, but the old issues will still be visible in the archive if you choose to publish it to GitHub pages.

### Do you support private repositories?
This should handle them correctly, and keep them private on GitHub. Right now the functionality is disabled if using GitHub to convert your repository (but should work if you use a local conversion tool), but you can find the place to enable it in the source code by searching for "vcs_username". The reason it is disabled is because it requires giving access credentials for BitBucket to GitHub (which some people may not like). This functionality hasn't been extensively tested though so you should absolutely check all permissions by hand after migration. I cannot be held responsible if use of this tool results in proprietary code leaking into the wild, and any consequences this may have to you or your employer.

I will eventually turn on the functionality by default, but it requires working out whether a repository is private and prompting the user to confirm it's OK to submit the credentials to GitHub. I just haven't had time to implement that yet, and if you feel like doing it for me I'll happily accept a pull request for it!

### How does this differ from existing migration tools?
Nothing I am aware of will migrate the repository, or archive pull-requests and commit comments in HTML pages.

This tool also does issue import better than other existing tools, as we are mapping between Git and mercurial hashes and have better rewrite rules between GitHub and BitBucket syntax.

### Will access permissions be copied across for our users?
No. You should always review access permissions when migrating between services. Don't ever rely to a 3rd party tool to get this right.

### What is the format for the BitBucket/GitHub username mapping?
It should be a JSON file of the format:

```
{
    "bitbucket_username1a": "associated GitHub username 1",
    "bitbucket_username1b": "associated GitHub username 1",
    "bitbucket_username2": "associated GitHub username 2"
}
```
Note that you can have multiple BitBucket usernames pointing to the same GitHub username (useful if someone has multiple BitBucket accounts but only one relevant GitHub account).

### What happens if I already have a repository on GitHub with a name that matches the repository I am importing from BitBucket
I have no idea, so make sure you don't ever do this. It might merge them. It might override your existing repository. It might throw an error. It probably won't be pretty.

### My git filepaths are too long when your tool clones my new git repositories. What do I do?
Run `git config --system core.longpaths true` from an elevated command prompt. See https://stackoverflow.com/questions/22575662/filename-too-long-in-git-for-windows

### I had multiple HEADS on a branch. They haven't made it to GitHub. What gives?
Yep, seems like the GitHub source importer can't handle multiple HEADS on a single branch. Some of your code will be lost in this case. If this matters to you, you should use a local tool to convert your mercurial repository to git (see above for tool suggestions).

## Contributing
Feel free to log issues, or make pull requests. I do not have a heap of time to spend solving issues, but I will do my best to help. **Pull requests improving the functionality (especially the quality of the archive template) are very welcome.**

## A note on code quality
The quality of this code is not great. Most of it was written during sleepless nights with a newborn baby. Since the usefulness of this tool will likely end in May 2020, I've focussed on getting something working rather than something that looks good and is maintainable. If you are a future employer reviewing this project, please don't judge me too harshly!