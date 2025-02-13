import re
import requests

from utils.api import ApiSession
from utils.phantom_constants import JIRA_AUTH

MIGRATED_KEYS = []


class BaseJiraApi:
    """
    Sets the base JIRA auth and SSL override for our internal servers
    """

    def __init__(self, base_url, override_credentials=None, test_credentials=False):
        self.session = ApiSession(base_url)

        if override_credentials:
            self.session.auth = override_credentials
        elif test_credentials:
            self.session.auth = ("admin", "phantom")
        else:
            self.session.auth = JIRA_AUTH


class JiraApi(BaseJiraApi):
    """
    Our internal phantom JIRA library. This should grow with time.
    """

    customer_facing_label = "customerfacing"
    specific_customer_label = "customer-"

    appname_label = "appname-"

    pam_subtask_type_id = "33"
    pam_pending_release_transition_id = "21"
    pam_to_qa_transition_id = "141"
    pam_subtask_released_transition_id = "31"

    papp_bug_type_id = "1"
    papp_severity_low = "18412"
    papp_resolved_state = "5"

    fields = {
        "severity": {"id": "customfield_16001", "pats": {"low": "18416"}},
        "tester": {"id": "customfield_16305"},
        "dev_shop": {
            "id": "customfield_16116",
            "phantom": "18220",
            "crest": "18221",
            "community": "18222",
            "customer": "18223",
            "partner": "18224",
        },
    }

    def __init__(self, test_credentials=False):
        if test_credentials:
            url = "http://10.1.16.180:8080/rest/api/2"
        else:
            url = "https://splunk.atlassian.net/rest/api/2"

        super().__init__(url, test_credentials=test_credentials)

    def get_all_projects(self, keys_only=False):
        resp = self.session.get("/project")
        resp.raise_for_status()

        if keys_only:
            return [project["key"] for project in resp.json()]

        return resp.json()

    def check_valid_username(self, username):
        resp = self.session.get("/user", params={"username": username})
        return resp.status_code == 200

    def get_issue_info(self, issue_key, expand_params=None):
        """
        Gets the information provided by JIRA for an issue
        Args:
            issue_key (basestring): The key or id of issue
            expand_params (list): a list of params to expand

        Returns:
            dictionary containing issue information
        """

        expand = ",".join(expand_params) if expand_params else ""
        resp = self.session.get(f"/issue/{issue_key}", params={"expand": expand})
        resp.raise_for_status()
        resp = resp.json()

        # split first on customer facing issues
        generic_customer_facing = False
        customer_labels = []
        appname_labels = []
        for label in resp["fields"]["labels"]:
            generic_customer_facing = generic_customer_facing or self.customer_facing_label in label

            if label.startswith(self.specific_customer_label):
                customer_labels.append(label)

            if label.startswith(self.appname_label):
                appname_labels.append(label)

        return {  # TODO really change this to return the jira response and have the application deal with the json
            "generic_customer_facing": generic_customer_facing,
            "customer_labels": customer_labels,
            "appname_labels": appname_labels,
            "summary": resp["fields"]["summary"],
            "key": resp["key"],
            "status": resp["fields"]["status"]["name"],
            "raw_json": resp,
        }

    def search_via_jql(self, jql_query, field_list=None):
        if isinstance(jql_query, list):
            # AND join the JQL query
            # if you want an order by, add it to the last element
            jql_query = " and ".join(jql_query)

        if field_list:
            resp = self.session.post("/search", json={"jql": jql_query, "fields": field_list})
        else:
            resp = self.session.post("/search", json={"jql": jql_query})

        try:
            resp.raise_for_status()  # TODO have an error handler deal with these things
        except Exception:
            print(resp)
            print(resp.text)
            raise
        return resp.json()

    def create_issue(
        self, project_key, summary, description, issue_type, subtask_parent, additional_fields=None
    ):
        assert isinstance(project_key, str)
        assert isinstance(summary, str)
        assert isinstance(description, str)
        assert isinstance(subtask_parent, str) or subtask_parent is None

        key = "id" if re.match(r"[0-9]+\Z", project_key) else "key"
        project = {key: project_key}

        key = "id" if re.match(r"[0-9]+\Z", issue_type) else "name"
        issue_type = {key: issue_type}

        jira_fields = additional_fields or {}
        jira_fields.update(
            {
                "project": project,
                "issuetype": issue_type,
                "summary": summary,
                "description": description,
            }
        )

        if subtask_parent:  # Handles the logic for creating the subtask, which is what we'll make for the app project
            key = "id" if re.match(r"[0-9]+\Z", subtask_parent) else "key"
            jira_fields["parent"] = {key: subtask_parent}

        resp = self.session.post("/issue", json={"fields": jira_fields})

        if resp.status_code == 201:
            return resp.json()["key"]
        else:
            return resp

    def edit_issue(self, issue_key, **kwargs):
        resp = self.session.put(f"/issue/{issue_key}", json=kwargs)
        if resp.status_code == 204:
            return issue_key

        return resp

    def transition_issue(self, issue_key, transition_id):
        post_data = {
            "transition": {"id": transition_id},
        }
        resp = self.session.post(f"/issue/{issue_key}/transitions", json=post_data)

        resp.raise_for_status()
        assert resp.status_code == 204

    def get_attachments(self, issue_key):
        resp = self.session.get(f"/issue/{issue_key}")
        resp.raise_for_status()

        return resp.json()["fields"]["attachment"]

    def add_attachment(self, issue_key, attachment_name, attachment_data):
        self.session.post(
            f"/issue/{issue_key}/attachments",
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (attachment_name, attachment_data)},
        )

    def remove_attachment(self, attachment_id):
        resp = self.session.delete(f"/attachment/{attachment_id}")
        return resp.status_code == 204

    def create_link(self, issue_link_type, inward_issue, outward_issue, **kwargs):
        key = "id" if re.match(r"[0-9]+\Z", issue_link_type) else "name"
        issue_link_type = {key: issue_link_type}

        key = "id" if re.match(r"[0-9]+\Z", inward_issue) else "key"
        inward_issue = {key: inward_issue}

        key = "id" if re.match(r"[0-9]+\Z", outward_issue) else "key"
        outward_issue = {key: outward_issue}

        kwargs.update(
            {
                "type": issue_link_type,
                "inwardIssue": inward_issue,
                "outwardIssue": outward_issue,
            }
        )

        resp = self.session.post("/issueLink", json=kwargs)

        resp.raise_for_status()

        return resp

    def get_pam_ticket_by_label(self, appname_label):
        jql_query = (
            f"project = PAM and labels in ({appname_label}) and issuetype = Task "
            "and status not in (Cancelled, Closed, Disqualified)"
        )

        pam_issues_with_label = self.search_via_jql(jql_query, field_list=["status"])
        assert pam_issues_with_label["total"] <= 1, (
            f"More than one PAM ticket found with label {appname_label}"
        )

        if pam_issues_with_label["total"]:
            return pam_issues_with_label["issues"][0]["key"]

        return None


class AgileApi(BaseJiraApi):
    """
    Internal GreenHopper Plug-in
    """

    def __init__(self, test_credentials=False):
        if test_credentials:
            url = "https://jira.corp.contoso.com/rest/agile/latest"
        else:
            url = "https://engineering.phantom.us/rest/agile/latest"

        super().__init__(url, test_credentials=test_credentials)

    def get_all_boards(self):
        resp = self.session.get("/board")
        resp.raise_for_status()

        return resp.json()["values"]

    def get_papp_board_id(self):
        boards = self.get_all_boards()
        for board in boards:
            if board["name"] == "Phantom Apps Board (PAPP)":
                return board["id"]
        else:
            raise ValueError("Could not find Phantom App Board")

    def get_papp_current_sprint(self):
        papp_board_id = self.get_papp_board_id()
        sprints = self.get_all_sprints(papp_board_id)

        return self.get_sprint_by_status("active", sprints)[0]["id"]

    def get_all_sprints(self, board_id):
        """
        A list of all the sprints per Project (boardName)
        """
        resp = self.session.get(f"/board/{board_id}/sprint")
        list_sprints = resp.json()["values"]
        resp.raise_for_status()
        while not resp.json()["isLast"]:
            resp = self.session.get(f"/board/{board_id}/sprint?startAt={len(list_sprints)}")
            resp.raise_for_status()
            list_sprints.extend(resp.json()["values"])

        return list_sprints

    def get_sprint_by_status(self, status, list_sprints):
        """
        A list of all the sprints by status key: ACTIVE or FUTURE
        """
        status_sprints = []
        if len(list_sprints) > 0:
            for sprint in list_sprints:
                if sprint.get("stateKey", sprint["state"]) == status:
                    status_sprints.append(sprint)

        return status_sprints

    def get_sprints_by_project(self, project_key, list_sprints):
        """
        A list of all the Sprints by Project key
        """
        project_keys = {
            "PPS": "Phantom Software Board (PS)",
            "PORT": "Portal Board (PORT)",
            "PATS": "PATS board",
            "PAPP": "Phantom Apps Board (PAPP)",
        }
        project_sprints = []
        if len(list_sprints) > 0:
            for sprint in list_sprints:
                print(sprint)
                if sprint["boardName"] == project_keys[project_key]:
                    project_sprints.append(sprint)

        return project_sprints


class DoubleSession(requests.Session):
    """
    Temporary class that allows JiraApi to work on two JIRA's simultaneously while projects are migrating.
    Use instead of ApiSession in the event of a migration to a new JIRA server in the future
    """

    def __init__(self, base_urls):
        assert len(base_urls) == 2
        self._sessions = [ApiSession(url) for url in base_urls]
        super().__init__()

    def request(self, method, url, **kwargs):
        if self._migrated(url, kwargs):
            try:
                resp = self._sessions[1].request(method, url, **kwargs)
                return resp
            except Exception:
                print(resp)
                print(resp.text)
                raise

        return self._sessions[0].request(method, url, **kwargs)

    def __getattr__(self, attr):
        if attr == "_sessions":
            return super().__getattr__(attr)
        return self._sessions[0].__getattribute__(attr)

    def __setattr__(self, attr, val):
        if attr == "_sessions":
            return super().__setattr__(attr, val)
        if isinstance(val, list) and len(val) == 2:
            self._sessions[0].__setattr__(attr, val[0])
            self._sessions[1].__setattr__(attr, val[1])
        else:
            for session in self._sessions:
                session.__setattr__(attr, val)

    def _migrated(self, url, opts):
        places_to_check = [url]

        try:
            places_to_check.append(opts["json"]["jql"])
        except KeyError:
            pass

        try:
            places_to_check.append(opts["json"]["fields"]["project"]["key"])
        except KeyError:
            pass

        result = any(key in place for key in MIGRATED_KEYS for place in places_to_check)
        return result


def create_jira_text_table(column_names, rows):
    table = ["{1}{0}{1}".format("||".join(column_names), "||")]
    column_size = len(column_names)
    for row in rows:
        assert len(row) == column_size, f"Row {row} does not match table columns"
        table.append("{1}{0}{1}".format("|".join(row), "|"))

    return "\n".join(table)
