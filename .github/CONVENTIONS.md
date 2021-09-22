# Conventions in use by ```splunk-soar-apps``` repositories

## Code and Style
The SOAR Apps team utilizes Python for its Apps. Consequently, we have standardized on [PEP8](https://www.python.org/dev/peps/pep-0008/) style. Additionally, as part of our automated checks, we run the [Flake8](http://flake8.pycqa.org/en/latest/) and [isort](https://pycqa.github.io/isort/) linters. The linting configuration used for our apps can be found [here](https://github.com/phantomcyber/dev-cicd-tools/blob/main/lint-configs/tox.ini).

We would ask that you follow these guidelines when developing your App to ensure consistency without our platform.

## App Naming Convetion
Our App directories follow the pattern of `ph`+`app-name`. For example, AWS IAM has the name "awsiam" and we prepend "ph" on the front, leaving us with `phawsiam` (always in lower case).  If you were going to create a new App for "Awesome API" then you would create a folder under `Apps` called `phawesomeapi`. Please feel free to look around in that directory to acquaint yourself with this pattern.

