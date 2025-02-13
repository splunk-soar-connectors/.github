from .github_utils import GithubCommunicator

ghc = GithubCommunicator(organization="phantomcyber")

rl = ghc.get_repos_data()
print(rl)

repo = ghc.create_repo(full_name="c_testrepo")

# print repo

result = ghc.get_teams_data()
print(result)

print(ghc.get_team("externally-developed-apps"))
ghc.add_team_to_repo(team_name="externally-developed-apps", repo_name="c_testrepo")
ghc.set_repo_permission(
    repo_name="c_testrepo", permission="admin", team_name="externally-developed-apps"
)
ghc.make_repo_private(repo_name="c_testrepo", private=True)
# ghc.create_issue(repo_name='c_testrepo', issue_content={'title': 'test title', 'body': 'some text\nnext line body\n'})

# ghc.add_collaborator_to_repo(collaborator_name='gunkl', repo_name='c_testrepo')

# ghc.add_collaborator_to_repo(collaborator_name='gunklasdfasdf', repo_name='c_testrepo')


# this doesnt work
# ghc.set_repo_permission(repo_name='c_testrepo', permission='admin', collaborator_name='gunkl')
