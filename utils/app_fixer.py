import os
import re
import datetime
import uuid
import random
from utils.api.jira import JiraApi
from utils.repo_tools import GitRepoModifier
from utils.app_parser import AppParser


class AppFixer:
    """
    Class used for automatic code fixes to app repositories
    """

    def __init__(self, app_name, repo_location, **kwargs):
        self._app_name = app_name
        self._app_code_dir = repo_location
        self._fix_ticket_number = None

        if "jira" in kwargs:
            self._jira = kwargs["jira"]
        else:
            self._jira = JiraApi()

        self.parser = AppParser(self._app_code_dir)
        self.repo_modifier = GitRepoModifier(self._app_code_dir)

        # Has a tendency to force 'next' branch when running locally. Commenting out rather than fixing because this code isn't really being used
        # test_branch = kwargs.pop('test_branch', 'next')
        # if test_branch not in self.repo_modifier.get_current_branch():
        #     self.repo_modifier.switch_to_branch(test_branch)

    def get_or_create_fix_ticket_number(self):
        if self._fix_ticket_number:
            return self._fix_ticket_number

        other_app_issues = self._jira.search_via_jql(
            'project = "Phantom App" and status = "In Progress" and Sprint in openSprints() order by created'
        )
        # TODO eventually check for currently active sprint (could cause bug)
        sprint_id = (
            other_app_issues["issues"][0]["fields"]["customfield_10005"][0]
            .split("id=")[1]
            .split(",")[0]
        )
        fix_version = other_app_issues["issues"][0]["fields"]["fixVersions"][0]["id"]
        test_run_time = datetime.datetime.now().strftime("%B %d %Y %I:%M %p")

        issue_title = f"Automated Test Fixes for {self._app_name}"
        other_automation_issues = self._jira.search_via_jql(
            f'project = "Phantom App" and summary ~ "{issue_title}" and created >= -1w and status = "Open"'
        )
        current_test_run = f"|{test_run_time}|"
        if len(other_automation_issues["issues"]) == 1:
            issue = other_automation_issues["issues"][0]
            resp = self._jira.edit_issue(
                issue["key"],
                fields={
                    "description": issue["fields"]["description"].replace("\r", "")
                    + f"\n{current_test_run}",
                },
            )
            assert isinstance(resp, str), f"{resp.status_code},{resp.text}"

            self._fix_ticket_number = issue["key"]
            return self._fix_ticket_number

        description = "\n".join(
            [
                "DO NOT EDIT BELOW THIS LINE",
                "||Human Readable Time||Tester||",
                current_test_run,
            ]
        )
        self._fix_ticket_number = self._jira.create_issue(
            project_key="PAPP",
            summary=f"Automated Test Fixes for {self._app_name} on {test_run_time}",
            description=description,
            issue_type=self._jira.papp_bug_type_id,
            subtask_parent=None,
            fields={
                "customfield_10103": {"value": "Medium"},
                "priority": {"name": "Normal"},
                "customfield_10005": int(sprint_id),
                "fixVersions": [{"id": fix_version}],
            },
        )
        assert isinstance(self._fix_ticket_number, str), "Something went wrong with the request"
        return self._fix_ticket_number

    def fix_app_json_key(self, repo_location, key, new_value, old_value=None):
        with open(os.path.join(repo_location, self.parser.app_json_name)) as f:
            raw_app_json = f.read()

        if old_value is None:
            old_value = self.parser.app_json[key]

        if "'" in old_value:
            new_app_json = re.sub(old_value, new_value, raw_app_json)
        else:
            new_app_json = re.sub(
                '"{}"[ ]*: "{}"'.format(key, old_value.replace("'", ".*")),
                f'"{key}": "{new_value}"',
                raw_app_json,
            )
        with open(os.path.join(repo_location, self.parser.app_json_name), "w") as f:
            f.write(new_app_json)  # doing it this way to keep order...easier

        self.parser.refresh_app_json(repo_location)

    def generate_and_commit_new_appid(self, local_repo_location):
        self.fix_app_json_key(local_repo_location, "appid", str(uuid.uuid4()).lower())
        self.repo_modifier.add_file_to_staging(self.parser.app_json_name)
        ticket = self.get_or_create_fix_ticket_number()
        self.repo_modifier.create_commit(f"{ticket} Package Name automated fix")

    def generate_app_version(self, local_repo_location):
        app_version = self.parser.app_json["app_version"]
        try:
            major, minor, build = (int(x) for x in app_version.strip().split("."))
        except ValueError:
            major, minor = (int(x) for x in app_version.strip().split("."))
            build = 0  # SAD
        if build > 999:
            minor += 1
            build = 0
        if minor > 10:
            major += 1
            minor = 0
        correct_version_number = f"{major}.{minor}.{build}"
        return correct_version_number

    def generate_action_description(self, local_repo_location):
        potential_actions = {action["type"] for action in self.parser.app_json["actions"]} - {
            "generic",
            "test",
        }
        product_name = self.parser.app_json["product_name"]
        if len(potential_actions) > 2:
            first_choice = random.choice(
                list(potential_actions)
            )  # todo prolly pick most popular action
            second_choice = random.choice(list(potential_actions - {first_choice}))
            new_description = f"This app implements {first_choice}, {second_choice} and other action types on {product_name}"
        elif len(potential_actions) == 2:
            first_choice, second_choice = tuple(potential_actions)
            new_description = f"This app implements various action types, such as {first_choice} and {second_choice}, on {product_name}"
        elif len(potential_actions):
            new_description = (
                f"This app implements {next(iter(potential_actions))} actions on {product_name}"
            )
        else:
            new_description = f"This app implements actions on {product_name}"
        return new_description
