# Community, Developer, and Splunk Supported Apps

You may have noticed that Splunk SOAR Apps can be community, developer, or Splunk-supported. This document explains what each level of support means.

App Tests  | Community Supported | Developer Supported | Splunk Supported
------------- | ------------- | ------------- | -------------
Static Tests | * | * | *
Compile Tests | * | * | *
Security Review | * | * | *
Linting | * | * | *
Code Review | * | * | *
Content Review |  | * | *
Full Functionality Testing | | Developer Dependent | *
Regular Regression Testing | | Developer Dependent | *

***

## Community Supported App Testing

### Static Tests
Checks for various connector code issues without executing the code

### Compile Tests
Checks that a connector successfully builds on a SOAR instance

### Security Review
Security checks are performed. The security review ensures no malicious code, libraries, or other content is published through the SOAR app listings.

### Linting
Enforces common Python code standards using standard tools

### Code Review
The SOAR Apps team conducts a code review.

### Content Review
The SOAR Apps team conducts a content review.  Common checks include:

* Would this update break existing user workflows?
* Is this update something that all users of the app can take advantage of?
* Does the update leverage an officially supported API on the partner product?
* Does this update expose functionality that is excessively difficult to use or understand?

### Passive Testing
The SOAR Apps team conducts "passive" tests which do not require access to the product integrated by the app.

For example:

* Does the app implement the required "test connectivity" action?
* Does the app properly handle exceptions?

## Developer Supported App Testing
The developer-supported apps should cover all tests done for community-supported apps. However, additional testing is typically done at the developer's discretion, and how they perform their testing may vary. Often developer supported apps will include their own version of full-functionality testing, regular regression testing, and more.

## Splunk-Supported App Testing
Splunk-supported apps build on top of all tests done for a community-supported app.  The main differentiating factor of a Splunk-supported app is that it has been thoroughly tested against a functional version of the product the app integrates with.

### Full Functionality Testing
For a Splunk-supported app, the SOAR QA team tests every action exposed by the app against a working version of the product.

### Regular Regression Testing
As part of the Splunk-supported app testing process, the SOAR QA team produces a SOAR Playbook that tests for app functionality.  This test is in turn used to perform regular regression testing of all Splunk-supported SOAR Apps. Tests are added when fixing bugs to ensure they are not reintroduced in the future.
