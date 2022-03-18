
Please ensure your pull request (PR) adheres to the following guidelines:

- Please refer to our contributing documentation for any questions on submitting a pull request, link: [Contribution Guide](https://github.com/splunk-soar-connectors/.github/blob/main/.github/CONTRIBUTING.md)

## Pull Request Checklist

#### Please check if your PR fulfills the following requirements:
- [ ] Testing of all the changes has been performed (for bug fixes / features)
- [ ] The readme.html has been reviewed and added / updated if needed (for bug fixes / features)
- [ ] Use the following format for the PR description: `<App Name>: <PR Type> - <PR Description>`
- [ ] Provide release notes as part of the PR submission which describe high level points about the changes for the upcoming GA release.
- [ ] Verify all checks are passing.
- [ ] Do NOT use the `next` branch of the forked repo. Create separate feature branch for raising the PR.
- [ ] Do NOT submit updates to dependencies unless it fixes an issue.
## Pull Request Type

#### Please check the type of change your PR introduces:
- [ ] New App
- [ ] Bugfix
- [ ] Feature
- [ ] Code style update (formatting, renaming)
- [ ] Refactoring (no functional changes, no api changes)
- [ ] Documentation
- [ ] Other (please describe): 

## Security Considerations (REQUIRED)
- [ ] If you are exposing any endpoints using a [REST handler](https://docs.splunk.com/Documentation/SOAR/current/DevelopApps/RESTHandlers), 
  please document them in the `readme.html`.
- [ ] If this is a new connector or you are adding new actions
    - [ ] Please document in the `readme.html` all methods (eg, OAuth) used to authenticate 
      with the service that the connector is integrating with.
    - [ ] If any actions are unable to run on SOAR Cloud, please document this in the `readme.html`.
- [ ] Are you introducing any new cryptography modules? If yes, please elaborate their purpose:
- [ ] Are you are accessing the file system? If yes, please verify that you are only accessing paths returned through 
the [Vault](https://docs.splunk.com/Documentation/SOARonprem/5.2.1/DevelopApps/AppDevAPIRef#Vault) API.
- [ ] Are you are marking code to be ignored by Semgrep with [`nosemgrep`](https://semgrep.dev/docs/ignoring-files-folders-code/#ignoring-code-through-nosemgrep)?
If yes, please provide justification in an additional comment next to the ignored code.

  

## Release Notes (REQUIRED)
- Provide release notes as part of the PR submission which describe high level points about the changes for the upcoming GA release.

## What is the current behavior? (OPTIONAL)
- Describe the current behavior that you are modifying.

## What is the new behavior? (OPTIONAL)
- Describe the behavior or changes that are being added by this PR.


## Other information (OPTIONAL)
- Any other information that is important to this PR such as screenshots of how the component looks before and after the change.

## Pay close attention to (OPTIONAL)
- Any specific code change or test case points which must be addressed/reviewed at the time of GA release.

## Screenshots (if relevant)

---
Thanks for contributing!
