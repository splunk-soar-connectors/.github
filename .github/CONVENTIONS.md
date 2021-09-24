# Conventions in use by ```splunk-soar-connectors``` repositories

## Code and Style
The SOAR Apps team utilizes Python for its Apps. Consequently, we have standardized on [PEP8](https://www.python.org/dev/peps/pep-0008/) style. Additionally, as part of our automated checks, we run the [Flake8](http://flake8.pycqa.org/en/latest/) and [isort](https://pycqa.github.io/isort/) linters. The linting configuration used for our apps can be found [here](https://github.com/phantomcyber/dev-cicd-tools/blob/main/lint-configs/tox.ini).

We would ask that you follow these guidelines when developing your App to ensure consistency without our platform.

## App Naming Convention
Our app repositories are named after the technologies they integrate in lowercase. For example, the repository for our [AWS Lambda](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html) app has the name "awslambda" (in lower case).  If you were going to create a new App for "Awesome API" then you would [request](https://github.com/splunk-soar-connectors/.github/issues/new?assignees=&labels=&template=new_repo_request.md&title=) that a new repo be created under the name `awesomeapi`. Please feel free to look around in the organization to acquaint yourself with this pattern.

