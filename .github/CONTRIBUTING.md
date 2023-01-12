# Contributing to Splunk SOAR

Thank you for considering spending your time contributing to Splunk SOAR. Whether you're interested in bug-hunting, documentation, or creating entirely new apps, this document will help and guide you through the process.

If you've stumbled upon the site but don't know who or what we are, please check out the links below:
- [Splunk > SOAR](https://www.splunk.com/en_us/software/splunk-security-orchestration-and-automation.html) - Home Page of Splunk SOAR
- [Phantom Community](https://my.phantom.us) - Splunk SOAR (formerly known as Phantom) Community site

---

## First Steps
Make sure you have a [GitHub Account](https://www.github.com)
- Make sure you know how Git works.
    - [Git Book](https://git-scm.com/book/en/v2)
    - [Git Handbook](https://guides.github.com/introduction/git-handbook/)
    - [GitHub Git Guide](https://help.github.com/en/articles/git-and-github-learning-resources)
    - [Git Workflow](https://guides.github.com/introduction/flow/)
    - [Git Visualization](http://git-school.github.io/visualizing-git/) -> Super cool!

## Project Details
To successfully contribute, you should spend a little time familiarizing yourself with the following key topics.

- [Coding & Conventions](https://github.com/splunk-soar-connectors/.github/blob/main/.github/CONVENTIONS.md) - How we expect to see code formatted and apps named
- [Various Types of Apps](https://github.com/splunk-soar-connectors/.github/blob/main/.github/TYPES_OF_SUPPORT.md) - definitions and differences
- [Typical developer workflow](https://github.com/splunk-soar-connectors/.github/blob/main/.github/DEV_WORKFLOW.md) - Configuring your dev environment
<!-- - [Testing Details](https://github.com/splunk-soar-connectors/.github/blob/main/.github/TESTING.md) - How we test apps & playbooks -->


## Step-by-Step Guide Available
If you are not familiar with a fork-and-branch Git workflow, or just feel a bit rusty on your Git knowledge, please check out our [step-by-step contribution guide](https://github.com/splunk-soar-connectors/.github/blob/main/.github/GUIDE.md) which has actual command line examples


# High Level Contribution Overview
## Contributing Bug-fixes
If you've found a bug and wish to fix it, the first thing to do is

1. [Fork](https://guides.github.com/activities/forking/) the app repo that you
are looking to contribute to.
1. Install [pre-commit](https://pre-commit.com/#install) on your system, if not already installed, and then run `pre-commit install` while inside the app repo. _Note: This step is not required, but **strongly** recommended! It will allow you to catch issues before even pushing any code._
1. Create a branch.
1. Make your changes on your branch.
1. Thoroughly test your changes. See the [Automated Checks](#automated-checks) section for information about basic automated checks we provide for all apps.
1. Add your name to the contributors list in the app JSON! [Example](https://github.com/phantomcyber/phantom-apps/pull/488/commits/a02e345ce48e56bcb8711d1c5c4e40dd6e62fd11?diff=split&w=1)
1. Open a [pull request](https://help.github.com/articles/using-pull-requests/) to the ```next``` branch of the app repo, giving edit access to the maintainers of the repo. Please ensure your pull request adheres to the guidelines mentioned in [PULL REQUEST TEMPLATE](https://github.com/splunk-soar-connectors/.github/blob/main/.github/pull_request_template.md).

*****Important Notes:**

1. **Please make sure to check the 'Allow edits and access to secrets by maintainers' box during your PR submission so that a Splunk>SOAR developer can aid in the PR process.**

1. **Any pull-request to the ```main``` branch of an app repo will not be accepted**

1. **A Splunk>SOAR developer may wish to create a new branch and ask you to perform your pull-request there for specific types of changes.**

1. **One issue per branch. We will not accept any Pull Requests that affect more than one App or addresses more than one Issue at a time (unless the issue is a duplicate - discretion of our development team).**

## Contributing New Apps

If you've created a brand new App and wish to contribute it, the steps to do so are as follows.

1. Create a new [issue](https://github.com/splunk-soar-connectors/.github/issues/new?assignees=&labels=&template=new_repo_request.md&title=) in our ```.github``` repo to request a new repository to be created for your app.
1. [Fork](https://guides.github.com/activities/forking/) the project.
1. Install [pre-commit](https://pre-commit.com/#install) on your system, if not already installed, and then run `pre-commit install` while inside the app repo. _Note: This step is not required, but **strongly** recommended! It will allow you to catch issues before even pushing any code._
1. Create a branch (following our [Conventions](https://github.com/splunk-soar-connectors/.github/blob/main/.github/CONVENTIONS.md)).
1. Push your app code to the branch you created. 
1. **Thoroughly** test your code for the new App. See the [Automated Checks](#automated-checks) section for information about basic automated checks we provide for all apps.
    <!-- 1. Ensure your new app has a [TESTING](https://about:blank) document for the community and our developers. -->
1. Add your name to the contributors list in the app JSON! [Example](https://github.com/phantomcyber/phantom-apps/pull/488/commits/a02e345ce48e56bcb8711d1c5c4e40dd6e62fd11?diff=split&w=1)
1. Perform a [pull request](https://help.github.com/articles/using-pull-requests/) to the ```next``` branch of the app repo. Please ensure your pull request adheres to the guidelines mentioned in [PULL REQUEST TEMPLATE](https://github.com/splunk-soar-connectors/.github/blob/main/.github/pull_request_template.md).

**Note: Any pull-request to the ```main``` branch of the app repo will not be accepted**

**Note: A Splunk>SOAR developer may wish to create a new branch and ask you to perform your pull-request there for specific types of changes.**

## Automated Checks
By default we provide various automated checks you can leverage to test your changes automatically. These checks will be run whenever you push new commits to your pull request branch. The overall pass/fail result will appear as a green checkmark or red "x" to the right of commit in the pull request page. To view the detailed report you can do **ANY** of the following:

- Click the checkmark or "x" and then click the "Details" link. **OR**
- Click the "Checks" tab at the top of the pull request. **OR**
- Click the "Details" link next to the list of checks that shows up at the bottom of the pull request. If the tests passed, this list will be hidden, so you will first need to click the "Show all checks" link.

Currently, some our automated checks are internal to Splunk and their details cannot be publicly viewed. The details for the following checks *can* be publicly accessed. 
 - **Linting** - [Flake8](http://flake8.pycqa.org/en/latest/) and [isort](https://pycqa.github.io/isort/) to ensure common Python coding standards are maintained.
 - **Semgrep** - a [static analysis tool](https://semgrep.dev/) to find potential vulnerabilitilies in app code.
 - **Static Tests** - common test suites that we run for each app repo. The "Details" link of this check will be a Google Drive link to the test results. A comment will also be posted in the PR with the same link by ```phantom-apps-bot```.
After doing any of the above, the results will be under the "Test pull request" section in the log that shows up. If the overall result was a failure this section will automatically be open and scrolled to the bottom of the report. Otherwise, clicking on "Test pull request" will open up the report.

## Legal Notice

By submitting a Contribution to this Work, You agree that Your Contribution is made subject to the primary license in the Apache 2.0 license (https://www.apache.org/licenses/LICENSE-2.0.txt). In addition, You represent that: (i) You are the copyright owner of the Contribution or (ii) You have the requisite rights to make the Contribution.

### Definitions:

“You” shall mean: (i) yourself if you are making a Contribution on your own behalf; or (ii) your company, if you are making a Contribution on behalf of your company. If you are making a Contribution on behalf of your company, you represent that you have the requisite authority to do so.

"Contribution" shall mean any original work of authorship, including any modifications or additions to an existing work, that is intentionally submitted by You for inclusion in, or documentation of, this project/repository. For the purposes of this definition, "submitted" means any form of electronic, verbal, or written communication submitted for inclusion in this project/repository, including but not limited to communication on electronic mailing lists, source code control systems, and issue tracking systems that are managed by, or on behalf of, the maintainers of the project/repository.

“Work” shall mean the collective software, content, and documentation in this project/repository.
