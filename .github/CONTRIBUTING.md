# Contributing to Splunk SOAR

Thank you for considering spending your time contributing to Splunk SOAR. Whether you're interested in bug-hunting, documentation, or creating entirely new apps, this document will help and guide you through the process.

If you've stumbled upon the site but don't know who or what we are, please check out the [Splunk SOAR](https://www.splunk.com/en_us/products/splunk-security-orchestration-and-automation.html) home page.

---

## First Steps
Make sure you have a [GitHub Account](https://www.github.com)
- Make sure you know how Git works.
    - [Git Book](https://git-scm.com/book/en/v2)
    - [Git Handbook](https://guides.github.com/introduction/git-handbook/)
    - [GitHub Git Guide](https://help.github.com/en/articles/git-and-github-learning-resources)
    - [Git Workflow](https://guides.github.com/introduction/flow/)
    - [Git Visualization](http://git-school.github.io/visualizing-git/) -> Super cool!

## Requesting New Apps
If you've created a new App and wish to contribute it:

1. Create a new [issue](https://github.com/splunk-soar-connectors/.github/issues/new?assignees=&labels=&template=new_repo_request.md&title=) in our ```.github``` repo to request a new repository to be created for your app.
1. Once the new repository has been created, follow the steps below.

## Contributing Changes to Apps
If you want to contribute a new action or a bug fix to an app:

1. [Fork](https://guides.github.com/activities/forking/) the app repo that you
are looking to contribute to.
1. Install [pre-commit](https://pre-commit.com/#install) on your system, if not already installed, and then run `pre-commit install` while inside the app repo.
1. Create a branch.
1. Make your changes on your branch.
1. Thoroughly test your changes. See the [Automated Checks](#automated-checks) section for information about basic automated checks we provide for all apps.
1. Add your name to the contributors list in the app JSON! [Example](https://github.com/phantomcyber/phantom-apps/pull/488/commits/a02e345ce48e56bcb8711d1c5c4e40dd6e62fd11?diff=split&w=1)
1. Open a [pull request](https://help.github.com/articles/using-pull-requests/) to the ```main``` branch of the app repo, giving edit access to the maintainers of the repo. Please ensure your pull request adheres to the guidelines mentioned in [PULL REQUEST TEMPLATE](https://github.com/splunk-soar-connectors/.github/blob/main/.github/pull_request_template.md).

## Python 3.13 Updates to Apps

Python 3.9 reaches end-of-life in October 2025. The SOAR platform's next release in early 2026 will require Python 3.13 compatibility for apps, and apps not updated will fail to execute.

### Actions to Ensure 3.13 Compatibility

1. Update app code for Python 3.13 compatibility (remove/update deprecated methods)
2. Verify dependencies support Python 3.13
3. Test in a Python 3.13 SOAR environment (7.0.0+)

### Automated Validation

Our pre-commit hooks validate Python 3.13 compatibility using the SOAR App Linter:

- Verifies `app.json` includes Python 3.13 in `python_version` field
- Detects removed modules, methods, and deprecated syntax
- Validates dependency compatibility

**Setup:**
```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

The SOAR App Linter job will run and raise any detected errors. Pre-commit validation must pass before pull requests can be merged.

## Project Details
To successfully contribute, you should spend a little time familiarizing yourself with the following key topics.

- [Coding & Conventions](https://github.com/splunk-soar-connectors/.github/blob/main/.github/CONVENTIONS.md) - How we expect to see code formatted and apps named
- [Various Types of Apps](https://github.com/splunk-soar-connectors/.github/blob/main/.github/TYPES_OF_SUPPORT.md) - Definitions and differences
- [Typical developer workflow](https://github.com/splunk-soar-connectors/.github/blob/main/.github/DEV_WORKFLOW.md) - Configuring your dev environment
<!-- - [Testing Details](https://github.com/splunk-soar-connectors/.github/blob/main/.github/TESTING.md) - How we test apps & playbooks -->


## Step-by-Step Guide Available
If you are not familiar with a fork-and-branch Git workflow, or just feel a bit rusty on your Git knowledge, please check out our [step-by-step contribution guide](https://github.com/splunk-soar-connectors/.github/blob/main/.github/GUIDE.md) which has actual command line examples


# High Level Contribution Overview


*****Important Notes:**

1. **Please make sure to check the 'Allow edits and access to secrets by maintainers' box during your PR submission so that a Splunk>SOAR developer can aid in the PR process.**

1. **A Splunk>SOAR developer may wish to create a new branch and ask you to perform your pull-request there for specific types of changes.**

1. **One issue per branch. We will not accept any Pull Requests that affect more than one App or addresses more than one Issue at a time (unless the issue is a duplicate - discretion of our development team).**
