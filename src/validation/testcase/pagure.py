# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import configparser
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ogr.abstract import CommitFlag, CommitStatus
from ogr.services.pagure import PagureProject

from validation.helpers import koji
from validation.testcase.base import Testcase

# Koji task states
KOJI_TASK_FREE = 0
KOJI_TASK_OPEN = 1
KOJI_TASK_COMPLETED = 2
KOJI_TASK_CANCELED = 3
KOJI_TASK_ASSIGNED = 4
KOJI_TASK_FAILED = 5


class KojiBuildWrapper:
    """Wrapper to make Koji build dict compatible with Copr build object interface."""

    def __init__(self, build_dict: dict):
        self._build = build_dict

    @property
    def id(self):
        """Return the build ID from the Koji build dict."""
        return self._build.get("build_id") or self._build.get("id")


class PagureTestcase(Testcase):
    """
    Testcase implementation for Pagure-based forges (src.fedoraproject.org, etc.).

    This testcase uses fedpkg for repository cloning and SSH keys for git operations.
    The Pagure account name defaults to 'packit-ci-test-bot' but can be overridden.

    Environment variables:
        PAGURE_ACCOUNT_NAME: Override the default account name (default: packit-ci-test-bot)
        PAGURE_SSH_KEY: Path to SSH private key for git operations
        PAGURE_TOKEN: API token for Pagure operations
        PAGURE_KEYTAB: Path to Kerberos keytab file for authentication

    Example usage:
        # Use default packit-ci-test-bot account
        export PAGURE_TOKEN=<packit-ci-test-bot-token>
        export PAGURE_SSH_KEY=/path/to/packit-ci-test-bot-key
        export PAGURE_KEYTAB=/path/to/keytab

        # Override to use a different account (e.g., for staging tests)
        export PAGURE_ACCOUNT_NAME=packit-stg
        export PAGURE_TOKEN=<packit-stg-token>
        export PAGURE_SSH_KEY=/path/to/packit-stg-key
        export PAGURE_KEYTAB=/path/to/keytab
    """

    project: PagureProject

    # Pagure is slower than GitHub/GitLab, increase timeout for build submission
    CHECK_TIME_FOR_SUBMIT_BUILDS = 10  # minutes

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._temp_dir = None
        self._fork = None
        self._config_dir = None

    @property
    def account_name(self):
        # Allow overriding account name via environment variable for testing
        # Default to packit-ci-test-bot
        return os.getenv("PAGURE_ACCOUNT_NAME", "packit-ci-test-bot")

    def get_status_name(self, status: CommitFlag) -> str:
        return status.context

    def construct_copr_project_name(self) -> str:
        """
        Not applicable for Pagure - uses Koji builds instead of Copr.
        This method is never called since check_build_submitted is overridden.
        """
        msg = "Pagure uses Koji builds, not Copr builds"
        raise NotImplementedError(msg)

    def _cleanup(self):
        """Clean up temporary directories created for git operations and config."""
        if self._temp_dir and Path(self._temp_dir).exists():
            logging.debug("Cleaning up temporary directory: %s", self._temp_dir)
            shutil.rmtree(self._temp_dir)
            self._temp_dir = None
        if self._config_dir and Path(self._config_dir).exists():
            logging.debug("Cleaning up config directory: %s", self._config_dir)
            shutil.rmtree(self._config_dir)
            self._config_dir = None

    def _setup_fedpkg_token(self):
        """
        Write PAGURE_TOKEN to fedpkg config file in a temporary directory.
        Returns the config file path to be used with --user-config option.
        """
        token = os.getenv("PAGURE_TOKEN")
        if not token:
            logging.warning("PAGURE_TOKEN not set, fedpkg commands may fail")
            return None

        # Create temporary config directory
        self._config_dir = tempfile.mkdtemp(prefix="validation-fedpkg-config-")
        config_file = Path(self._config_dir) / "fedpkg.conf"

        # Create config with token
        config = configparser.ConfigParser()
        config.add_section("fedpkg.distgit")
        config.set("fedpkg.distgit", "token", token)

        # Write config
        with open(config_file, "w") as f:
            config.write(f)

        logging.debug("Wrote PAGURE_TOKEN to %s", config_file)
        return str(config_file)

    def _get_authenticated_username(self):
        """Get the authenticated user's username."""
        if not hasattr(self, "_username"):
            self._username = self.project.service.user.get_username()
            logging.debug("Authenticated user: %s", self._username)
        return self._username

    def _ensure_fork_exists(self):
        """Ensure a fork exists under the authenticated user."""
        user = self._get_authenticated_username()

        # Try to create the fork, ignore error if it already exists
        try:
            logging.info("Creating fork under user %s", user)
            self.project.fork_create()
            logging.info("Fork created successfully")
        except Exception as e:
            # Fork likely already exists
            if "already exists" in str(e):
                logging.debug("Fork already exists under user %s", user)
            else:
                # Some other error, re-raise
                raise

    def _setup_git_repo(self):
        """Clone the repository using fedpkg and set up fork."""
        if self._temp_dir is None:
            # Set up fedpkg token from environment variable
            config_file = self._setup_fedpkg_token()

            # Create parent temporary directory
            parent_temp = tempfile.mkdtemp(prefix="validation-pagure-")
            logging.debug("Created temporary directory: %s", parent_temp)

            # Check for SSH key to use for git operations
            ssh_key_path = os.getenv("PAGURE_SSH_KEY")
            if ssh_key_path and os.path.exists(ssh_key_path):
                logging.debug("Will use SSH key: %s", ssh_key_path)
            else:
                logging.warning(
                    "PAGURE_SSH_KEY not set or file doesn't exist, SSH authentication may fail",
                )

            # Use fedpkg to clone
            package_name = self.project.repo
            logging.info("Cloning %s using fedpkg", package_name)

            # Build fedpkg command with config file if available
            clone_cmd = ["fedpkg"]
            if config_file:
                clone_cmd.extend(["--user-config", config_file])
            clone_cmd.extend(["clone", "-a", package_name])

            try:
                subprocess.run(  # noqa: S603
                    clone_cmd,
                    cwd=parent_temp,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self._temp_dir = str(Path(parent_temp) / package_name)
                logging.debug("Repository cloned to: %s", self._temp_dir)
            except subprocess.CalledProcessError as e:
                logging.error("Failed to clone with fedpkg: %s\nstderr: %s", e, e.stderr)
                shutil.rmtree(parent_temp)
                raise

            # Ensure fork exists via API
            self._ensure_fork_exists()

            # Use fedpkg to set up fork remote
            logging.info("Setting up fork with fedpkg")
            user = self.account_name

            # Build fedpkg fork command with config file if available
            fork_cmd = ["fedpkg"]
            if config_file:
                fork_cmd.extend(["--user-config", config_file])
            fork_cmd.append("fork")

            try:
                subprocess.run(  # noqa: S603
                    fork_cmd,
                    cwd=self._temp_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # fedpkg fork creates HTTPS remote, but we need SSH for Kerberos
                # Change the fork remote URL to SSH
                hostname = self.project.service.hostname
                if hostname == "src.fedoraproject.org":
                    git_hostname = "pkgs.fedoraproject.org"
                else:
                    git_hostname = hostname

                ssh_url = f"ssh://{user}@{git_hostname}/forks/{user}/{self.project.namespace}/{self.project.repo}.git"
                logging.debug("Configuring fork remote to SSH: %s", ssh_url)

                # Check if remote exists, create if not
                check_remote = subprocess.run(  # noqa: S603
                    ["git", "remote", "get-url", user],  # noqa: S607
                    cwd=self._temp_dir,
                    capture_output=True,
                    check=False,
                )

                if check_remote.returncode == 0:
                    # Remote exists, update URL
                    subprocess.run(  # noqa: S603
                        ["git", "remote", "set-url", user, ssh_url],  # noqa: S607
                        cwd=self._temp_dir,
                        check=True,
                        capture_output=True,
                    )
                    logging.debug("Updated existing fork remote")
                else:
                    # Remote doesn't exist, create it
                    subprocess.run(  # noqa: S603
                        ["git", "remote", "add", user, ssh_url],  # noqa: S607
                        cwd=self._temp_dir,
                        check=True,
                        capture_output=True,
                    )
                    logging.debug("Created new fork remote")

                # Configure git to use SSH key if provided
                ssh_key_path = os.getenv("PAGURE_SSH_KEY")
                if ssh_key_path:
                    ssh_command = f"ssh -i {ssh_key_path} -o IdentitiesOnly=yes"
                    subprocess.run(  # noqa: S603
                        ["git", "config", "core.sshCommand", ssh_command],  # noqa: S607
                        cwd=self._temp_dir,
                        check=True,
                        capture_output=True,
                    )
                    logging.debug("Configured git to use SSH key: %s", ssh_key_path)

            except subprocess.CalledProcessError as e:
                logging.warning("Fork setup failed: %s\nstderr: %s", e, e.stderr)

        return self._temp_dir

    def create_file_in_new_branch(self, branch: str):
        """Create a new branch and add a file using git operations."""
        repo_dir = self._setup_git_repo()

        # Create and checkout new branch
        logging.info("Creating branch: %s", branch)
        subprocess.run(  # noqa: S603
            ["git", "checkout", "-b", branch],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

        # Create a test file
        test_file = Path(repo_dir) / "test.txt"
        test_file.write_text("Testing the opened PR trigger.")

        # Add and commit the file
        subprocess.run(
            ["git", "add", "test.txt"],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Opened PR trigger"],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        logging.debug("Created commit with test file")

        # Push to fork (fedpkg fork creates remote named after Kerberos principal)
        # Use deployment account name which should match the Kerberos principal
        fork_remote = self.account_name
        logging.info("Pushing branch %s to fork remote '%s'", branch, fork_remote)
        try:
            # Push to fork with upstream tracking (force push to overwrite existing test branch)
            subprocess.run(  # noqa: S603
                ["git", "push", "--force", "--set-upstream", fork_remote, branch],  # noqa: S607
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            logging.info("Successfully pushed branch to fork")
        except subprocess.CalledProcessError as e:
            logging.error("git push failed: %s\nstderr: %s", e, e.stderr)
            raise

    def create_pr(self):
        """Override create_pr to implement Pagure-specific PR creation."""
        source_branch = f"test/{self.deployment.name}/opened_pr"
        pr_title = f"Basic test case ({self.deployment.name}): opened PR trigger"
        logging.info("Creating new PR: %s from branch %s", pr_title, source_branch)
        self.delete_previous_branch(source_branch)

        # Delete the PR from the previous test run if it exists
        existing_pr = [pr for pr in self.project.get_pr_list() if pr.title == pr_title]
        if len(existing_pr) == 1:
            logging.debug("Closing existing PR: %s", existing_pr[0].url)
            existing_pr[0].close()

        logging.debug("Creating file in new branch: %s", source_branch)
        self.create_file_in_new_branch(source_branch)
        if self.deployment.opened_pr_trigger__packit_yaml_fix:
            self.fix_packit_yaml(source_branch)

        logging.debug("Creating PR via Pagure API...")
        # For Pagure, we need to create PR from the fork to the parent project
        self._ensure_fork_exists()

        # Get fork username to specify where the branch is located
        fork_username = self.account_name

        # Since OGR's Pagure create_pr is not implemented, call the API directly
        # When creating PR from a fork, must specify repo_from parameters
        pr_data = self.project._call_project_api(
            "pull-request",
            "new",
            method="POST",
            data={
                "title": pr_title,
                "branch_to": self.project.default_branch,
                "branch_from": source_branch,
                "initial_comment": (
                    "This test case is triggered automatically by our validation script."
                ),
                "repo_from": self.project.repo,
                "repo_from_username": fork_username,
                "repo_from_namespace": self.project.namespace,
            },
        )

        # Get the created PR
        from ogr.services.pagure.pull_request import PagurePullRequest

        # PagurePullRequest expects the raw PR data, not just the ID
        self.pr = PagurePullRequest(raw_pr=pr_data, project=self.project)
        self.head_commit = self.pr.head_commit
        logging.info("PR created: %s", self.pr.url)

    def get_statuses(self) -> list[CommitFlag]:
        # Filter by the Packit service account that sets commit statuses
        # This is the same as the Copr user (packit for prod, packit-stg for staging)
        packit_user = self.deployment.copr_user
        return [
            status
            for status in self.project.get_commit_statuses(commit=self.head_commit)
            if status._raw_commit_flag.get("user", {}).get("name") == packit_user
        ]

    def is_status_successful(self, status: CommitFlag) -> bool:
        return status.state == CommitStatus.success

    def is_status_completed(self, status: CommitFlag) -> bool:
        return status.state not in [
            CommitStatus.running,
            CommitStatus.pending,
        ]

    def is_status_recent(self, status: CommitFlag) -> bool:
        """
        Check if the status was created after the build was triggered.
        Uses created timestamp with a 1-minute buffer for clock skew.
        """
        if not self._build_triggered_at:
            return True  # No trigger time set, accept all statuses
        if not status.created:
            return True  # No timestamp on status, accept it

        # Convert naive datetime to UTC-aware if needed
        status_time = self._ensure_aware_datetime(status.created)

        # Allow 1 minute buffer for clock skew
        buffer_time = self._build_triggered_at - timedelta(minutes=1)
        return status_time >= buffer_time

    def delete_previous_branch(self, branch: str):
        """Delete a branch from the fork."""
        try:
            # Set up git repo if not already done (this ensures fork exists)
            repo_dir = self._setup_git_repo()

            # Get fork remote name (should match deployment account)
            fork_remote = self.account_name

            # Try to delete the branch via git push
            logging.info("Attempting to delete branch %s from fork", branch)
            try:
                subprocess.run(  # noqa: S603
                    ["git", "push", fork_remote, f":{branch}"],  # noqa: S607
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                logging.info("Deleted branch %s from fork", branch)
            except subprocess.CalledProcessError as e:
                logging.warning("Could not delete branch %s: %s\nstderr: %s", branch, e, e.stderr)
        except Exception as e:
            logging.warning("Error deleting branch %s: %s", branch, e)

    def update_file_and_commit(self, path: str, commit_msg: str, content: str, branch: str):
        """Update a file and commit the changes using git operations."""
        repo_dir = self._setup_git_repo()

        # Checkout the branch
        logging.info("Checking out branch: %s", branch)
        subprocess.run(  # noqa: S603
            ["git", "checkout", branch],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

        # Update the file
        file_path = Path(repo_dir) / path
        file_path.write_text(content)

        # Add and commit
        subprocess.run(  # noqa: S603
            ["git", "add", path],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603
            ["git", "commit", "-m", commit_msg],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        logging.debug("Updated file %s and committed", path)

        # Push to fork (fedpkg fork creates remote named after deployment account)
        fork_remote = self.account_name
        logging.info("Pushing updated branch %s to fork", branch)
        subprocess.run(  # noqa: S603
            ["git", "push", "--force", fork_remote, branch],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    def _get_packit_yaml_ref(self, branch: str) -> str:  # noqa: ARG002
        """
        Override to read .packit.yaml from default branch.
        The test branch only exists in the fork, not in the main repo.
        """
        return self.project.default_branch

    def create_empty_commit(self, branch: str, commit_msg: str) -> str:
        """Create an empty commit using git operations."""
        repo_dir = self._setup_git_repo()

        # Checkout the branch
        logging.info("Checking out branch: %s", branch)
        subprocess.run(  # noqa: S603
            ["git", "checkout", branch],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

        # Create empty commit
        subprocess.run(  # noqa: S603
            ["git", "commit", "--allow-empty", "-m", commit_msg],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

        # Get commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        commit_sha = result.stdout.strip()
        logging.debug("Created empty commit: %s", commit_sha)

        # Push to fork (fedpkg fork creates remote named after deployment account)
        fork_remote = self.account_name
        logging.info("Pushing branch %s to fork", branch)
        subprocess.run(  # noqa: S603
            ["git", "push", "--force", fork_remote, branch],  # noqa: S607
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        return commit_sha

    async def check_build_submitted(self):
        """
        Check whether the Koji build task was submitted.
        Overrides the base class Copr build check for Pagure projects.
        Scratch builds don't appear in listBuilds(), so we query listTasks() instead.
        """
        logging.info("Checking Koji build submission for Pagure PR")
        old_comment_len = len(list(self.pr.get_comments())) if self.pr else 0

        self._build_triggered_at = datetime.now(tz=timezone.utc)
        self.trigger_build()

        watch_end = datetime.now(tz=timezone.utc) + timedelta(
            minutes=self.CHECK_TIME_FOR_SUBMIT_BUILDS,
        )

        await self.check_pending_check_runs()

        logging.info(
            "Watching for Koji build task submission for %s (timeout: %d minutes)",
            self.pr,
            self.CHECK_TIME_FOR_SUBMIT_BUILDS,
        )

        check_count = 0
        while True:
            check_count += 1
            if check_count % 5 == 0:
                logging.debug("Still waiting for Koji build submission (check #%d)...", check_count)
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += (
                    "The Koji build was not submitted in time "
                    f"({self.CHECK_TIME_FOR_SUBMIT_BUILDS} minutes).\n"
                )
                return

            # Query Koji for build tasks matching our commit
            koji_task = self.get_koji_task_for_pr()

            if koji_task:
                self._build = KojiBuildWrapper({"build_id": koji_task["id"], "id": koji_task["id"]})
                logging.info("Found Koji task: %s", koji_task["id"])
                return

            # Check for error comments from packit-service
            new_comments = list(self.pr.get_comments(reverse=True))
            new_comments = new_comments[: (len(new_comments) - old_comment_len)]

            if new_comments:
                packit_comments = [
                    comment.body for comment in new_comments if comment.author == self.account_name
                ]
                if packit_comments:
                    comment_text = packit_comments[0]
                    self.failure_msg += (
                        f"New comment from packit-service while submitting Koji build: "
                        f"{comment_text}\n"
                    )

            await asyncio.sleep(120)

    async def check_build(self, build_id):
        """
        Check whether the Koji task completed successfully.
        Overrides the base class Copr build check for Pagure projects.
        :param build_id: ID of the Koji task (for Pagure, this is a task ID not a build ID,
                         since scratch builds are tasks not builds)
        :return:
        """
        task_id = build_id  # For Pagure, build_id is actually a task_id
        watch_end = datetime.now(tz=timezone.utc) + timedelta(minutes=self.CHECK_TIME_FOR_BUILD)
        state_reported = None
        logging.info(
            "Watching Koji task %s (timeout: %d minutes)",
            task_id,
            self.CHECK_TIME_FOR_BUILD,
        )

        koji_session = koji()

        while True:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += (
                    f"The Koji task did not finish in time ({self.CHECK_TIME_FOR_BUILD} minutes).\n"
                )
                return

            task_info = koji_session.getTaskInfo(task_id)
            task_state = task_info.get("state")

            # Koji task states:
            # 0 = FREE
            # 1 = OPEN (running)
            # 2 = CLOSED (completed successfully)
            # 3 = CANCELED
            # 4 = ASSIGNED
            # 5 = FAILED

            if task_state == state_reported:
                await asyncio.sleep(self.POLLING_INTERVAL * 60)
                continue

            state_reported = task_state
            state_names = {
                KOJI_TASK_FREE: "FREE",
                KOJI_TASK_OPEN: "OPEN",
                KOJI_TASK_COMPLETED: "CLOSED",
                KOJI_TASK_CANCELED: "CANCELED",
                KOJI_TASK_ASSIGNED: "ASSIGNED",
                KOJI_TASK_FAILED: "FAILED",
            }
            logging.debug(
                "Koji task %s state: %s",
                task_id,
                state_names.get(task_state, task_state),
            )

            if task_state == KOJI_TASK_COMPLETED:
                # Task completed successfully
                logging.info("Koji task %s completed successfully", task_id)
                return
            if task_state in [KOJI_TASK_CANCELED, KOJI_TASK_FAILED]:
                # Task failed or canceled
                logging.error(
                    "Koji task %s failed with state: %s",
                    task_id,
                    state_names.get(task_state, task_state),
                )
                state = state_names.get(task_state, task_state)
                self.failure_msg += f"The Koji task was not successful. Koji state: {state}.\n"
                return

            await asyncio.sleep(self.POLLING_INTERVAL * 60)

    def get_package_name(self) -> str:
        """
        Get the package name from the project (e.g., 'requre').
        """
        return self.project.repo

    def get_koji_task_for_pr(self) -> dict | None:
        """
        Get Koji build task associated with this PR's commit.
        Scratch builds are tasks, not builds, so we query listTasks() instead of listBuilds().
        We match tasks by commit hash in the source URL, not by package name.
        """
        koji_session = koji()

        try:
            # Query recent build tasks for this package
            # Method 'build' is the standard build task type
            tasks = koji_session.listTasks(
                opts={
                    "method": "build",
                    "decode": True,
                    "state": [
                        KOJI_TASK_FREE,
                        KOJI_TASK_OPEN,
                        KOJI_TASK_COMPLETED,
                        KOJI_TASK_CANCELED,
                        KOJI_TASK_ASSIGNED,
                    ],
                },
                queryOpts={"limit": 20, "order": "-id"},
            )

            # Filter tasks that match our commit
            for task in tasks:
                if self.is_task_for_pr(task):
                    return task

            return None
        except Exception as e:
            logging.warning("Error fetching Koji tasks: %s", e)
            return None

    def is_task_for_pr(self, task: dict) -> bool:
        """
        Check if a Koji task is associated with this PR's commit.
        The task request contains the git URL with the commit hash.
        """
        try:
            koji_session = koji()
            # Get the task request which contains the source URL
            request = koji_session.getTaskRequest(task["id"])

            # Request format: [source_url, target, opts]
            # Example: ['git+https://src.fedoraproject.org/forks/...', 'rawhide', {...}]
            if request and len(request) > 0:
                source_url = request[0]
                # Check if our commit hash is in the source URL
                # The commit hash uniquely identifies our build
                if self.head_commit in source_url:
                    logging.debug("Task %s matches commit %s", task["id"], self.head_commit)
                    return True
        except Exception as e:
            logging.debug("Error checking task %s: %s", task.get("id"), e)

        return False
