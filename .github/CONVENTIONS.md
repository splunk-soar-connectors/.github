# Conventions in use by ```splunk-soar-connectors``` repositories

## Code and Style
The SOAR Apps team utilizes Python for its Apps. Consequently, we have standardized on [PEP8](https://www.python.org/dev/peps/pep-0008/) style. Additionally, as part of our automated checks, we run the [ruff](https://docs.astral.sh/ruff/) linter. The linting configuration used for our apps can be found [here](https://github.com/phantomcyber/dev-cicd-tools/blob/main/templates/pyproject.toml). Running pre-commit will ensure you conform to these standards; See CONTRIBUTING.md for steps on running this.

We would ask that you follow these guidelines when developing your App to ensure consistency without our platform.

## Commit Messages
We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification for our commit messages. This provides a standardized format that makes the commit history more readable and enables automated generation of changelogs.

The basic structure is:

<type>[optional scope]: <description>
[optional body]
[optional footer(s)]

Common types include:
- feat: A new feature
- fix: A bug fix
- docs: Documentation changes
- style: Changes that don't affect code functionality (formatting, etc.)
- refactor: Code changes that neither fix bugs nor add features
- test: Adding or modifying tests
- chore: Changes to build process or auxiliary tools

Please follow this convention when contributing to our repositories.

## App Naming Convention
Our app repositories are named after the technologies they integrate in lowercase. For example, the repository for our [AWS Lambda](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html) app has the name "awslambda" (in lower case).  If you were going to create a new App for "Awesome API" then you would [request](https://github.com/splunk-soar-connectors/.github/issues/new?assignees=&labels=&template=new_repo_request.md&title=) that a new repo be created under the name `awesomeapi`. Please feel free to look around in the organization to acquaint yourself with this pattern.
