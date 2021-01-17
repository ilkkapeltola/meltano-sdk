"""Sample tap stream test for tap-gitlab."""

import copy
from pathlib import Path
from singer_sdk.typehelpers import (
    ArrayType,
    DateTimeType,
    IntegerType,
    PropertiesList,
    StringType,
)
from singer_sdk import helpers
from singer_sdk.authenticators import SimpleAuthenticator
from typing import Any, Dict, List, Optional, Union

from singer_sdk.streams.rest import RESTStream

SCHEMAS_DIR = Path("./singer_sdk/samples/sample_tap_gitlab/schemas")

DEFAULT_URL_BASE = "https://gitlab.com/api/v4"


class GitlabStream(RESTStream):
    """Sample tap test for gitlab."""

    @property
    def url_base(self) -> str:
        return self.config.get("api_url", DEFAULT_URL_BASE)

    @property
    def authenticator(self) -> SimpleAuthenticator:
        """Return an authenticator for REST API requests."""
        http_headers = {"Private-Token": self.config.get("auth_token")}
        if self.config.get("user_agent"):
            http_headers["User-Agent"] = self.config.get("user_agent")
        return SimpleAuthenticator(stream=self, http_headers=http_headers)

    def get_params(self, substream_id: str) -> Dict[str, Any]:
        """Expose any needed config values into the URL parameterization process.

        If a list of dictionaries is returned, one call will be made for each item
        in the list. For GitLab, this is necessary when each call must reference a
        specific `project_id`.
        """
        project_id = substream_id.split(",")[0].split("=")[1]  # Ex "project_id=foo/bar"
        return {"project_id": project_id, "start_date": self.config.get("start_date")}


class ProjectBasedStream(GitlabStream):
    """Base class for streams that are keys based on project ID."""

    def get_substream_ids(self) -> List[str]:
        """Return a list of substream IDs (if applicable), otherwise None."""
        return [f"project_id={id}" for id in self.config.get("project_ids")]


class ProjectsStream(ProjectBasedStream):
    name = "projects"
    path = "/projects/{project_id}?statistics=1"
    primary_keys = ["id"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "projects.json"


class ReleasesStream(ProjectBasedStream):
    name = "releases"
    path = "/projects/{project_id}/releases"
    primary_keys = ["project_id", "commit_id", "tag_name"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "releases.json"


class IssuesStream(ProjectBasedStream):
    name = "issues"
    path = "/projects/{project_id}/issues?scope=all&updated_after={start_date}"
    primary_keys = ["id"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "issues.json"


class CommitsStream(ProjectBasedStream):
    name = "commits"
    path = (
        "/projects/{project_id}/repository/commits?since={start_date}&with_stats=true"
    )
    primary_keys = ["id"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "commits.json"


class EpicsStream(ProjectBasedStream):
    name = "epics"
    path = "/groups/{group_id}/epics?updated_after={start_date}"
    primary_keys = ["id"]
    replication_key = None
    schema = PropertiesList(
        IntegerType("id"),
        IntegerType("iid"),
        IntegerType("group_id"),
        IntegerType("parent_id", optional=True),
        StringType("title", optional=True),
        StringType("description", optional=True),
        StringType("state", optional=True),
        IntegerType("author_id", optional=True),
        DateTimeType("start_date", optional=True),
        DateTimeType("end_date", optional=True),
        DateTimeType("due_date", optional=True),
        DateTimeType("created_at", optional=True),
        DateTimeType("updated_at", optional=True),
        ArrayType("labels", wrapped_type=StringType),
        IntegerType("upvotes", optional=True),
        IntegerType("downvotes", optional=True),
    ).to_dict()

    # schema_filepath = SCHEMAS_DIR / "epics.json"

    def post_process(self, row: dict) -> dict:
        """Perform post processing, including queuing up any child stream types."""
        helpers.ensure_stream_state_exists(
            self.state, "epic_issues", substream=f"epic={row['id']}"
        )
        return super().post_process(row)


class EpicIssuesStream(GitlabStream):
    """EpicIssues stream class.

    NOTE: This should only be run after epics have been synced, since epic streams
          have a dependency on the state generated by epics.
    """

    name = "epic_issues"
    path = "/groups/{group_id}/epics/{epic_id}/issues"
    primary_keys = ["id"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "epic_issues.json"
    parent_stream_types = [EpicsStream]  # Stream should wait for parents to complete.

    def get_params(self, substream_id: str) -> dict:
        """Return http params for a stream or substream."""
        substream_state = helpers.read_stream_state(
            self.state, (self.name, substream_id)
        )
        if "epic_id" not in substream_state:
            raise ValueError("Cannot sync epic issues without already known epic IDs.")
        result = super().get_params(substream_id)
        result.update(substream_state)
        return result