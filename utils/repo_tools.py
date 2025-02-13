import os

from collections import Counter
from datetime import datetime

from utils.command_utils import get_command_output
from contextlib import contextmanager

from utils.phantom_constants import APP_DEV_TEAM_EMAIL_LIST, APP_DEV_OVERLORD_EMAIL


class GitRepoModifier:
    def __init__(self, repo_location):
        self.repo_location = repo_location
        self.git_repo_location = os.path.join(repo_location, ".git")

    def git_cmd(self, command):
        assert isinstance(command, list)
        return ["git", "--git-dir", self.git_repo_location, *command]

    @staticmethod
    def sc(command):
        """
        Stringify command for easy copy/paste for debugging
        """
        if isinstance(command, list):
            return " ".join(command)

        return command

    def get_earliest_modified_date_for_file(self, file_path):
        """
        Args:
            file_path: relative file path from repo toplevel

        Returns:
            Earliest year as a string
        """
        return str(
            datetime.fromtimestamp(
                float(
                    get_command_output(
                        f'cd {self.repo_location} && git log -1 --reverse --format="%at" {file_path}',
                        shell=True,
                    ).strip()
                )
            )
            .date()
            .year
        )

    def get_status(self):
        return get_command_output(self.git_cmd(["status"]))

    def get_all_branches(self):
        """
        Gets a list of the current branches
        Returns:
            List of branch strings, with the current branch preceded by `* `
        """
        return get_command_output(self.git_cmd(["branch", "-a"])).rstrip().split("\n")

    def get_current_branch(self):
        branch_list = self.get_all_branches()
        for branch in branch_list:
            if branch.startswith("* "):
                return branch.partition("* ")[2]
        else:
            raise ValueError("Could not get current branch!")

    def get_current_commit(self):
        return get_command_output(self.git_cmd(["rev-parse", "HEAD"])).rstrip("\n")

    def create_new_branch(self, branch_name):
        command = self.git_cmd(["checkout", "-q", "-b", branch_name])
        return get_command_output(command)

    def switch_to_branch(self, branch_name):
        assert any(branch_name in branch for branch in self.get_all_branches()), (
            f"Does {branch_name} exist?"
        )
        return get_command_output(
            f"cd {self.repo_location} && git checkout {branch_name}", shell=True
        )

    def get_most_common_author(self, app_team_only=True):
        command = self.git_cmd(["log", '--format="%ae"'])
        counter = Counter()
        for author in [
            email.replace('"', "") for email in get_command_output(command).strip().split("\n")
        ]:
            counter[author] += 1

        if not app_team_only:
            most_common_author = counter.most_common(1)[0][0]
        else:
            for author, _ in counter.most_common():
                if author in APP_DEV_TEAM_EMAIL_LIST:
                    most_common_author = author
                    break
            else:
                most_common_author = APP_DEV_OVERLORD_EMAIL
        return most_common_author

    def add_file_to_staging(self, *files):
        # todo stop shelling out
        return get_command_output(
            "cd {} && git add {}".format(self.repo_location, " ".join(files)), shell=True
        )

    def create_commit(self, message):
        """
        Creates a commit on the current git branch
        Args:
            message(str): The commit message

        Returns:
            the generated commit hash
        """
        get_command_output(self.git_cmd(["commit", "-m", message]))
        return get_command_output(self.git_cmd(["rev-parse", "HEAD"])).rstrip("\n")

    def push_branch(self, branch_name):
        command = self.git_cmd(["push", "--set-upstream", "origin", branch_name])
        return get_command_output(command, quiet_stderr=True)

    @contextmanager
    def create_and_push_branch(self, branch_name):
        """
        Creates a new branch from the current branch.
        If changes are made, a corresponding remote branch is made and pushed.
        At the end of the function, we return to the old branch
        Args:
            branch_name: name of new branch

        Returns:

        """
        old_branch = self.get_current_branch()
        old_branch_latest_commit = self.get_current_commit()

        self.create_new_branch(branch_name)
        yield branch_name

        if self.get_current_commit() != old_branch_latest_commit:
            self.push_branch(branch_name)

        self.switch_to_branch(old_branch)
