import argparse
import functools
import os
import shutil
import uuid

from github import Github, GithubException

from .credential_helper import get_credential


def github_exception(function):
    """
    A decorator that wraps the passed in function and logs
    exceptions should one occur
    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as e:
            # log the exception
            try:
                errval = e[0]
            except Exception:
                errval = None
            err = f"=> ERROR {errval}: " + function.__name__ + " - "
            try:
                errdata = e[1]
                err += "Message: {}".format(errdata.get("message"))
                err += "\n--> Doc url: {}".format(errdata.get("documentation_url"))
            except Exception as ef:
                err += f"\nSecondary exception: {ef!s}"
            err += f"\n--> Args: {kwargs}"
            err += f"\n--> Raw: {e!s}\n"
            print(err)

            raise

    return wrapper


class GithubCommunicator:
    """
    Set up a Github connection instance
    ghc = GithubCommunicator(organization='phantomcyber')
    """

    @github_exception
    def __init__(
        self, credname=None, test_credentials=False, organization="phantomcyber", repository=None
    ):
        """
        Initializes the Github connection.

        :param credname: Key value of the credential found in .app_rel_creds that can be used to authenticate on Github.
        :param test_credentials: True if testing credentials after initialization, False otherwise.  Default False.
        :param organization: Organization to perform functions from.  Default phantomcyber.
        :param repository: Repository to retrieve or create.

        :returns: None

        :raises: Github.UnknownObjectException
        """
        if not credname:
            gituser = get_credential(credname="phgit_community", key="user")
            gitpass = get_credential(credname="phgit_community", key="token")
        else:
            gituser = get_credential(credname=credname, key="user")
            gitpass = get_credential(credname=credname, key="token")
        self.ghb = Github(gituser, gitpass)
        self.ghb_user = self.ghb.get_user()
        self.ghb_org = self.ghb.get_organization(organization)
        self.ghb_team = None
        self.ghb_repo = self.get_repo(repository)

        if test_credentials:
            repos = self.ghb_user.get_repos()
            if repos:
                print(
                    f"Pass: Got a list of repos- Count: {len(repos.get_page(0))} - First repo name: {repos[0]}"
                )
            else:
                print(f"Fail: Returned: {repos} (not a repo list!)")

        super().__init__()

    @github_exception
    def get_repos_data(self, datatype=None):
        if datatype == "object":
            return self.ghb_user.get_repos()
        else:
            result = []
            for repo in self.ghb_user.get_repos():
                result.append({"id": repo.id, "name": repo.name})
            return result
        return None

    @github_exception
    def get_teams_data(self, datatype=None):
        if datatype == "object":
            return self.ghb_org.get_teams()
        else:
            result = []
            for team in self.ghb_org.get_teams():
                result.append({"id": team.id, "name": team.name})
            return result
        return None

    @github_exception
    def get_repo(self, name=None, id=None, force=False):
        """
        Retrieves a Github Repository, if available, from the Organization.

        :param name: String name of the repository to retrieve
        :param id: Int ID of the repository to retrieve
        :param force: True if force creation, False otherwise.

        :returns: Github.Repo object, None otherwise

        :raises: None
        """
        for repo in self.ghb_org.get_repos():
            if name == repo.name:
                self.ghb_repo = repo
                return repo
            if id == repo.id:
                self.ghb_repo = repo
                return repo
        if force:
            return self.create_repo(name)

    @github_exception
    def get_team(self, name=None, id=None):
        # get team by name or ID
        # also set team object
        for team in self.ghb_org.get_teams():
            if name == team.name:
                self.ghb_team = team
                return team.id
            if id == team.id:
                self.ghb_team = team
                return team.name
        return None

    @github_exception
    def get_collaborator(self, name=None):
        # get collaborator by name
        # also set collaborator object
        for collaborator in self.ghb_repo.get_collaborators():
            # print collaborator
            if name == collaborator.login:
                self.ghb_collaborator = collaborator
                return collaborator.login

    @github_exception
    def get_github_object(self):
        return self.ghb

    @github_exception
    def create_repo(self, full_name=None):
        # repo = ghc.create_repo(full_name='c_testrepo')
        # repo path becomes: https://github.com/phantomcyber/c_testrepo
        repo = self.ghb_org.create_repo(full_name)
        return repo

    @github_exception
    def add_team_to_repo(self, team_name=None, repo_name=None):
        # permission is str and can be pull, push, or admin
        # ghc.add_team_to_repo(team_name='externally-developed-apps', repo_name='c_testrepo')
        self.get_team(name=team_name)
        self.get_repo(name=repo_name)
        self.ghb_team.add_to_repos(self.ghb_repo)

    @github_exception
    def add_collaborator_to_repo(self, collaborator_name=None, repo_name=None):
        # unfortunately this adds collaborators with the default permissions of "write" access
        # collaborators should only have "read" - pygithub doesn't support setting this permission yet, it appears.
        # permission is str and can be pull, push, or admin
        # ghc.add_collaborator_to_repo(collaborator_name='somegithubusername', repo_name='c_testrepo')
        self.get_repo(name=repo_name)
        self.ghb_repo.add_to_collaborators(collaborator_name)

    @github_exception
    def make_repo_private(self, repo_name=None, private=True):
        # make a repo private or public
        # ghc.make_repo_private(repo_name='c_testrepo', private=True)
        self.get_repo(name=repo_name)
        self.ghb_repo.edit(private=private)

    @github_exception
    def set_repo_permission(
        self, repo_name=None, permission=None, team_name=None, collaborator_name=None
    ):
        # permission is str and can be pull, push, or admin
        # team_name, or collaborator_name are optional, one or the other can be specified to set the permission
        # ghc.set_repo_permission(repo_name='c_testrepo', permission='admin', team_name='externally-developed-apps')
        self.get_repo(name=repo_name)
        if team_name:
            self.get_team(name=team_name)
            self.ghb_team.set_repo_permission(repo=self.ghb_repo, permission=permission)
        # this doesn't work and doesn't appear to be supported.
        # elif collaborator_name:
        #     self.get_collaborator(name=collaborator_name)
        #     self.ghb_collaborator.set_repo_permission(repo=self.ghb_repo, permission=permission)

    @github_exception
    def create_issue(self, repo_name=None, issue_content=None):
        # kwargs can be title, body, assignee, milestone, and label
        # ghc.create_issue(repo_name='c_testrepo', issue_content={'title': 'test title', 'body': 'some text\nnext line body\n'})
        if issue_content is None:
            issue_content = {}
        self.get_repo(name=repo_name)
        self.ghb_repo.create_issue(**issue_content)

    def create_repo_and_push(self, app_dir, collaborator, update=False):
        """
        Creates a Github Repository within the Organization in the class.  Pulls it locally, and then adds the directory
        to it.

        :param app_dir: Absolute path of the directory that contains the app that is attempting to be pushed.
        :param collaborator: Email of the contributor to add as a collaborator to the Repo being created/updated.
        :param update: True if an update to a previously-submitted third-party-app, False otherwise.  Default False.

        :returns: URL to repository

        :raises: UnknownObjectException if the repository does not exist.
        """

        name = os.path.basename(app_dir)
        repository = self.get_repo(name=name, force=True)

        temp_dir = self._create_unique_dir()

        try:
            self._clone_repo(repository, name, temp_dir, app_dir)

        except GithubException:  # Excepts here if the repository is empty.
            if not update:
                self._init_new_repo(app_dir, temp_dir, repository.ssh_url)

            else:
                print("Unable to update app code as a Repository for the app is not available.")
                raise ValueError(
                    "Unable to update app code as a Repository for the app is not available."
                ) from None
        finally:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir)

            repository.edit(private=True)

            if not update:
                repository.add_to_collaborators(collaborator)

        return repository.url

    def _create_unique_dir(self):
        """
        Creates a unique directory in /tmp.

        :returns: String name of unique directory
        """

        unique_id = uuid.uuid4().hex
        temp_dir = f"/tmp/{unique_id}"
        if os.path.exists(temp_dir):
            return self._create_unique_dir()
        else:
            os.mkdir(temp_dir)
            return temp_dir

    def _clone_repo(self, repository, repo_name, destination, app_dir):
        """
        Clones the repo in the destination directory.  Then takes the files from the passed app directory and adds them
        to both the local and remote repositories.

        :param repository: Github.Repo object
        :param repo_name: Name of the app (must match exactly)
        :param destination: Absolute path of the directory to clone into
        :param app_dir: Absolute path of the app source code

        :raises: Github.GithubException if the Repository cloned is empty
        """

        repository.get_contents(repo_name)
        os.system(f"cd {destination} && git clone {repository.ssh_url}")
        for dirpath, _dirs, files in os.walk(app_dir):
            for filename in files:
                shutil.copy(os.path.join(dirpath, filename), destination)
        os.system(
            f"cd {destination} && git add . && git commit -am 'Phantom QA commit' && git push -u origin master"
        )

    def _init_new_repo(self, src, dst, ssh_url):
        """
        Initializes a new Repository locally.  This repository should not have any contents to begin with.
        Sets the origin correctly before pushing to master branch on remote.

        :param src: Source code to add to the Repository
        :param dst: Destination for the local Repository
        :param ssh_url: SSH URL to set origin to

        :raises: Nothing yet
        """
        real_dst = os.path.join(dst, os.path.basename(src))
        if os.path.exists(real_dst):
            shutil.rmtree(real_dst)
        shutil.copytree(src, real_dst)
        os.system(f"cd {real_dst} && git init && git add . && git commit -am 'initial commit'")
        os.system(f"cd {real_dst} && git remote add origin {ssh_url} && git push -u origin master")


if __name__ == "__main__":
    argp = argparse.ArgumentParser(
        description="Pretty wrapper for the pyGithub objects using Phantom-specific values.",
        usage=argparse.SUPPRESS,
    )
    required_args = argp.add_argument_group("required arguments")
    required_args.add_argument(
        "-r", "--repository", help="Name of the repository to create or connect to.", required=True
    )
    required_args.add_argument(
        "-k",
        "--key",
        help="Value of the key within the credential manager to be used to authenticate over Github",
        required=True,
    )
    argp.add_argument(
        "-c", "--collaborator", help="Name of the collaborator to add to the Repository."
    )
    argp.add_argument(
        "-o",
        "--organization",
        help="Name of the Organization to use.  Default is phantomcyber.",
        default="phantomcyber",
    )
    argp.add_argument("-d", "--directory", help="Directory of the third-party-app to push.")
    argp.add_argument(
        "-u",
        "--update",
        help="Flag to determine whether this is an update to a previously-submitted third-party-app.",
        action="store_true",
    )
    args = argp.parse_args()

    connection = GithubCommunicator(
        repository=args.repository, organization=args.organization, credname=args.key
    )
    if not args.collaborator or not args.directory:
        print("Collaborator or args not specified.  Exiting with nothing to do.")
        exit(-1)
    else:
        connection.create_repo_and_push(args.directory, args.collaborator, args.update)
