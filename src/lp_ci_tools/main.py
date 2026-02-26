#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from concurrent import futures
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from pathlib import Path
from random import shuffle
from uuid import uuid4

import yaml
from launchpadlib.launchpad import Launchpad

logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger("lp-ci-tools")


class MissingBranchError(Exception):
    pass


def git(
    *args,
    return_raw=False,
    return_output=False,
    check=True,
    working_dir: str | None = None,
):
    """Call a git command."""
    command = ["git", *args]
    logger.info(" ".join(command))
    if return_raw:
        return subprocess.run(command, cwd=working_dir, capture_output=True, text=True)
    elif return_output:
        return subprocess.check_output(command, cwd=working_dir).decode().strip()
    elif check:
        return subprocess.check_call(command, cwd=working_dir)
    else:
        return subprocess.call(command, cwd=working_dir)


def login_to_lp(credentials):
    if credentials:
        lp = Launchpad.login_with(
            "launchpad-ci", "production", credentials_file=credentials, version="devel"
        )
    else:
        lp = Launchpad.login_anonymously("launchpad-ci", "production", version="devel")
    return lp


def get_repository(lp, repo_path):
    return lp.git_repositories.getByPath(path=repo_path)


def get_prerequisite_proposals(proposal):
    """
    Given a proposal, return all prerequisite proposals that are not in
    the superseded state.  There ideally should be one and only one here
    (or zero), but sometimes there are not, depending on developer habits.
    """
    prerequisite_repo = proposal.prerequisite_git_repository
    prerequisite_ref = proposal.prerequisite_git_path
    # We only consider MPs which are against the same target repo
    # and reference (eg. "main").
    target_repo = proposal.target_git_repository
    target_ref = proposal.target_git_path
    if (
        not prerequisite_repo
        or not prerequisite_ref
        or not target_repo.landing_candidates
    ):
        return []

    prerequisite_repo_name = prerequisite_repo.unique_name
    # Go through the list of MPs against the target repository and
    # ensure they are against the same ref and that they match the
    # prerequisite repo/ref.
    return [
        mp
        for mp in target_repo.landing_candidates
        if (
            mp.target_git_path == target_ref
            and mp.source_git_repository.unique_name == prerequisite_repo_name
            and mp.source_git_path == prerequisite_ref
            and mp.queue_status != "Superseded"
        )
    ]


def is_approved(proposal):
    target = proposal.target_git_repository
    votes = proposal.votes
    for vote in votes:
        if not target.isPersonTrustedReviewer(reviewer=vote.reviewer):
            continue
        if vote.is_pending:
            continue
        if vote.comment.vote == "Approve":
            return True
    return False


def get_latest_commit_sha1(proposal):
    branch = proposal.source_git_repository.getRefByPath(path=proposal.source_git_path)
    if branch is None:
        raise MissingBranchError()
    return branch.commit_sha1


def extract_commit_from_comment(comment):
    regex = r"COMMIT: \b(?P<sha1>[0-9a-f]{5,40})\b"
    matches = re.search(regex, comment.message_body)
    if matches is None:
        return None
    return matches.group("sha1")


def has_test_marker(comment):
    for line in comment.message_body.splitlines():
        if line.startswith("jenkins: !test"):
            return True
    return False


def generate_mergable_proposals(lp, git_repo):
    approved_proposals = list(git_repo.getMergeProposals(status="Approved"))
    shuffle(approved_proposals)
    for proposal in approved_proposals:
        if not is_approved(proposal):
            continue
        if not proposal.commit_message:
            job_info = get_job_info(proposal)
            branch_info = get_branch_info(job_info)
            subject = f"Re: [Merge] {branch_info} - MISSING COMMIT MESSAGE"
            comment = "UNABLE TO START LANDING\n\nSTATUS: MISSING COMMIT MESSAGE"
            proposal.createComment(subject=subject, content=comment)
            proposal.setStatus(status="Needs review")
            proposal.lp_save()
            continue

        prereqs = get_prerequisite_proposals(proposal)
        if len(prereqs) == 1 and prereqs[0].queue_status != "Merged":
            continue

        yield proposal


def _get_mp(lp, git_repo_path, proposal_address, repo_logger):
    git_repo = lp.git_repositories.getByPath(path=git_repo_path)
    proposal_idx = {
        mp["address"]: i for i, mp in enumerate(git_repo.landing_candidates.entries)
    }
    try:
        return git_repo.landing_candidates[proposal_idx[proposal_address]]
    except IndexError:
        repo_logger.warning(f"Having to hydrate proposals for {git_repo.display_name}")
        for proposal in git_repo.landing_candidates:
            if proposal.address == proposal_address:
                return proposal


def should_review(args, git_repo_path, proposal_address, repo_logger):
    lp = login_to_lp(args.credentials)
    proposal = _get_mp(lp, git_repo_path, proposal_address, repo_logger)
    repo_logger.debug(f"Considering {proposal.web_link}")
    hit_test_marker = False
    reviewed_commits = []
    for comment in proposal.all_comments:
        if has_test_marker(comment):
            hit_test_marker = True
        if lp.me == comment.author:
            commit = extract_commit_from_comment(comment)
            if commit:
                reviewed_commits.append(commit)
                if hit_test_marker:
                    # Test marker was set but a following comment holds a test, so
                    # the test has already been ran for this proposal.
                    hit_test_marker = False
    if hit_test_marker:
        repo_logger.debug(f"Found marker on {proposal.web_link}; Testing!")
        return True
    # If WIP, see if this has been marked to be tested.
    if proposal.queue_status == "Work in progress":
        repo_logger.debug(f"Skipping {proposal.web_link}: WiP but no marker found")
        return False
    try:
        # See if the latest commit has already been reviewed.
        latest_commit = get_latest_commit_sha1(proposal)
    except MissingBranchError:
        repo_logger.debug(f"Skipping {proposal.web_link}: branch has been deleted")
        return False
    if latest_commit in reviewed_commits:
        repo_logger.debug(
            f"Skipping {proposal.web_link}: {latest_commit} has already been reviewed"
        )
        return False
    else:
        return True


def generate_reviewable_proposals(args, git_repo, repo_logger):
    needs_review_proposals = list(git_repo.getMergeProposals(status="Needs review"))
    wip_proposals = list(git_repo.getMergeProposals(status="Work in progress"))
    shuffle(needs_review_proposals)
    shuffle(wip_proposals)
    ex = futures.ThreadPoolExecutor()
    results = ex.map(
        lambda mp: (
            mp
            if should_review(args, git_repo.unique_name, mp.address, repo_logger)
            else None
        ),
        chain(needs_review_proposals, wip_proposals),
    )

    for proposal in results:
        if proposal:
            repo_logger.debug(f"Testing {proposal.web_link} !")
            yield proposal


def get_nice_repo_name(repo):
    if repo.target_default:
        return repo.unique_name.split("+")[0][:-1]
    else:
        return repo.unique_name


def get_job_info(proposal):
    branch_dest = proposal.target_git_path.split("refs/heads/")[1]
    branch_src = proposal.source_git_path.split("refs/heads/")[1]
    author = proposal.registrant
    if author.hide_email_addresses:
        source_prefix = f"git+ssh://{os.getenv('SSHUSER')}@git.launchpad.net/"
        source_remote = (
            f"{source_prefix}{get_nice_repo_name(proposal.source_git_repository)}"
        )
        source_remote_name = f"source{uuid4()}"
        os.chdir("maas-ci-internal")
        git("remote", "add", source_remote_name, source_remote)
        git("fetch", source_remote_name, branch_src)
        # get the author from the last commit, remove " and \n chars
        email = re.sub(
            r"\"|\n",
            "",
            git(
                "show",
                '--format="%aE"',
                f"{source_remote_name}/{branch_src}",
                "-q",
                return_output=True,
            ),
        )
        git("remote", "remove", source_remote_name)
        os.chdir("..")
    else:
        email = author.preferred_email_address.email
    return {
        "LP_REPO_SRC": get_nice_repo_name(proposal.source_git_repository),
        "LP_BRANCH_SRC": branch_src,
        "LP_REPO_DEST": get_nice_repo_name(proposal.target_git_repository),
        "LP_BRANCH_DEST": branch_dest,
        "LP_COMMIT_MSG": proposal.commit_message,
        "LP_COMMIT_SHA1": get_latest_commit_sha1(proposal),
        "LP_MP_LINK": proposal.web_link,
        "LP_MP_AUTHOR_NAME": author.display_name,
        "LP_MP_AUTHOR_EMAIL": email,
        "MP": proposal,
    }


def generate_jobs(proposals):
    for proposal in proposals:
        try:
            yield get_job_info(proposal)
        except Exception:
            logger.debug(f"Skipping unknown job info for {proposal.web_link}")


def is_job(job, job_info):
    for key in ["LP_REPO_SRC", "LP_REPO_DEST", "LP_BRANCH_SRC", "LP_BRANCH_DEST"]:
        if (
            (job_val := job.get(key))
            and (job_info_val := job_info.get(key))
            and (job_val != job_info_val)
        ):
            return False
    return True


def find_proposal(lp, job_info):
    source_repo = get_repository(lp, job_info["LP_REPO_SRC"])
    source_ref = source_repo.getRefByPath(path=job_info["LP_BRANCH_SRC"])
    for job in generate_jobs(source_ref.landing_targets):
        if is_job(job, job_info):
            return job["MP"]


def get_branch_info(job_info):
    return (
        f"-b {job_info['LP_BRANCH_SRC']} lp:{job_info['LP_REPO_SRC']}"
        f" into -b {job_info['LP_BRANCH_DEST']} lp:{job_info['LP_REPO_DEST']}"
    )


def mark_bugs_fix_committed(proposal):
    project = proposal.target_git_repository.target
    if project.resource_type_link != "https://api.launchpad.net/devel/#project":
        return
    series_name = proposal.target_git_path.split("refs/heads/")[1]
    series = project.getSeries(name=series_name)
    if not series:
        series = project.development_focus
    if not series:
        return

    def find_task_for_target(bug, target):
        for task in bug.bug_tasks:
            if task.target == target:
                return task
        return None

    for bug in proposal.bugs:
        task = find_task_for_target(bug, series)
        if not task and series == project.development_focus:
            task = find_task_for_target(bug, project)
        if task:
            task.status = "Fix Committed"
            set_milestone_on_task(project, task)
            task.lp_save()


def find_branch(repo, branch_name: str):
    for branch in repo.branches:
        if branch.path == f"refs/heads/{branch_name}":
            return branch


def run_make_format_and_commit(working_dir: str) -> None:
    """Installs dependencies, runs make format and commit the result if there's a diff.

    Since we moved to ruff, older branches might need to reformat the commit
    with black and isort.
    """
    logger.info("Formatting the commit")
    try:
        subprocess.call(["make", "install-dependencies"], cwd=working_dir)
        subprocess.call(["go", "mod", "vendor"], cwd=f"{working_dir}/src/maasagent")
        subprocess.call(["make", "format"], cwd=working_dir)
        if git("diff", "--quiet", check=False, working_dir=working_dir):
            git("add", "-u", working_dir=working_dir)
            git("commit", "--amend", "--no-edit", check=False, working_dir=working_dir)
    except subprocess.CalledProcessError:
        logger.error("Failed to commit auto formatting.")


def markdown_link(text: str) -> str:
    _, linktype, number = text.rsplit("/", 2)
    return f"[{linktype.replace('+', '')}:{number}]({text})"


def cherry_pick_failure(cherry_output: str) -> dict[str, str]:
    result = {"type": "unknown", "action": "fix something", "raw": cherry_output}

    def extract_file(line: str) -> str:
        reason, further_text = line.split(":", 1)
        return f"{reason.strip()} {further_text.strip().split()[0]}"

    if (
        "conflict" in cherry_output
        or "needs merge" in cherry_output
        or "could not apply" in cherry_output
    ):
        result["type"] = "merge conflict"
        result["action"] = "resolve conflicts: " + "; ".join(
            [
                extract_file(line)
                for line in cherry_output.splitlines()
                if ("conflict" in line or "needs merge" in line)
                and not line.startswith("hint")
            ]
        )

    elif (
        "resolve your current index" in cherry_output or "in progress" in cherry_output
    ):
        result["type"] = "dirty worktree"
        result["action"] = "reset working tree"

    elif "bad revision" in cherry_output or "not a valid object" in cherry_output:
        result["type"] = "invalid commit"
        result["action"] = "check commit hash"

    elif (
        "not enough commits" in cherry_output
        or "could not find commit" in cherry_output
    ):
        result["type"] = "missing commits"
        result["action"] = "check commit hash existence"

    elif "empty" in cherry_output or "nothing to commit" in cherry_output:
        result["type"] = "empty cherry pick"
        result["action"] = "skip this commit"

    elif "permission denied" in cherry_output or "index.lock" in cherry_output:
        result["type"] = "filesystem error"
        result["action"] = "check permissions or remove lock"

    elif "corrupt" in cherry_output or "missing" in cherry_output:
        result["type"] = "corrupted repo"
        result["action"] = "clean clone the repo"

    return result


def set_milestone_on_task(project, task):
    """
    Attempt to auto-determine the milestone to set, and set the milestone
    of the given task.  If the task already has a milestone set => noop.
    Only processed if config setting `set_milestone` == True.
    """
    task_milestone = task.milestone
    if task_milestone is not None:
        return
    now = datetime.utcnow()
    target_milestone = find_target_milestone(project, now)
    task.milestone = target_milestone


def find_target_milestone(project, now):
    """
    Find a target milestone when resolving a bug task.

    Compare the selected datetime `now` to the list of milestones.
    Return the milestone where `targeted_date` is newer than the given
    datetime.  If the given time is greater than all open milestones:
    target to the newest milestone in the list.

    In this algorithm, milestones without targeted dates appear lexically
    sorted at the end of the list.  So the lowest sorting one will get
    chosen if all milestones with dates attached are exhausted.

    In other words, pick one of the milestones for the target.  Preference:
        1) closest milestone (by date) in the future
        2) least lexically sorting milestone (by name)
        3) the last milestone in the list (covers len()==1 case).
    """
    earliest_after = latest_before = untargeted = None
    for milestone in project.active_milestones:
        if milestone.date_targeted is None:
            if untargeted is not None:
                if milestone.name < untargeted.name:
                    untargeted = milestone
            else:
                untargeted = milestone
        elif milestone.date_targeted > now:
            if earliest_after is not None:
                if earliest_after.date_targeted > milestone.date_targeted:
                    earliest_after = milestone
            else:
                earliest_after = milestone
        elif milestone.date_targeted < now:
            if latest_before is not None:
                if latest_before.date_targeted < milestone.date_targeted:
                    latest_before = milestone
            else:
                latest_before = milestone
    if earliest_after is not None:
        return earliest_after
    elif untargeted is not None:
        return untargeted
    else:
        return latest_before


def _get_launchpad_ci_files(jobs_cfg_dir: Path):
    launchpad_ci_files = []
    # First pass of the yaml files to look for projects that are
    # `{name}-launchpad-ci`, meaning they use launchpad-ci.yaml's
    # job-group
    grep = subprocess.run(
        ["grep", "-l", "--", "-launchpad-ci"] + list(jobs_cfg_dir.glob("*.yaml")),
        text=True,
        check=True,
        capture_output=True,
    )
    for line in grep.stdout.splitlines():
        launchpad_ci_files.append(Path(line))
    return launchpad_ci_files


@dataclass
class Repo:
    name: str
    series: str
    lp_path: str


def generate_repos(jobs_cfg_dir):
    yaml_files = _get_launchpad_ci_files(jobs_cfg_dir)
    shuffle(yaml_files)
    for yaml_file in yaml_files:
        data_logger = logging.getLogger(yaml_file.name)
        try:
            with yaml_file.open() as fh:
                data = yaml.safe_load(fh)[0]
        except (yaml.YAMLError, IndexError):
            data_logger.error("Unable to load job config")
            continue
        else:
            try:
                project = data["project"]
                name = project["name"]
                lp_path = project["repo_lp_path"]
                series = project.get("ubuntu_series")
            except KeyError:
                data_logger.error(
                    "...doesn't look like a launchpad-ci config, skipping"
                )
                continue
            else:
                repo = Repo(name, lp_path=lp_path, series=series)
                data_logger.debug(f"Found {repo}")
                yield repo, data_logger


def handle_reviewable_jobs(args, lp):
    """Check for MPs that can be reviewed."""
    job_list = []
    for repo, repo_logger in generate_repos(args.jobs_cfg_dir):
        git_repo = get_repository(lp, repo.lp_path)
        if git_repo is None:
            repo_logger.error(f"Unable to load git repo at {repo.lp_path}")
            continue
        for proposal in generate_reviewable_proposals(args, git_repo, repo_logger):
            job = get_job_info(proposal)
            del job["MP"]
            job["NAME"] = repo.name
            job["SERIES"] = repo.series
            job_list.append(job)
    logger.debug(job_list)
    print(json.dumps(job_list))
    return 0


def handle_mergable_jobs(args, lp):
    """Check for MPs that can be merged."""
    job_list = []
    for repo, repo_logger in generate_repos(args.jobs_cfg_dir):
        git_repo = get_repository(lp, repo.lp_path)
        if git_repo is None:
            repo_logger.error(f"Unable to load git repo at {repo.lp_path}")
            continue
        for proposal in generate_mergable_proposals(lp, git_repo):
            job = get_job_info(proposal)
            del job["MP"]
            job["NAME"] = repo.name
            job["SERIES"] = repo.series
            job_list.append(job)
    print(json.dumps(job_list))
    return 0


def handle_mark_mp(args, lp):
    job_info = {
        "LP_REPO_SRC": args.repo_src,
        "LP_BRANCH_SRC": args.branch_src,
        "LP_REPO_DEST": args.repo_dest,
        "LP_BRANCH_DEST": args.branch_dest,
        "LP_COMMIT_SHA1": args.commit,
    }
    branch_info = get_branch_info(job_info)
    proposal = find_proposal(lp, job_info)
    if not proposal:
        print("Unable to find merge proposal.")
        return 1

    if args.start_review:
        lp.me = lp.me
        for vote in proposal.votes:
            if lp.me == vote.reviewer:
                # Already a reviewer.
                return 0

        # New review set as the person running unit tests.
        proposal.nominateReviewer(review_type="unittests", reviewer=lp.me)
        proposal.lp_save()
        return 0

    if args.fail_review:
        subject = f"Re: [UNITTESTS] {branch_info} - TESTS FAILED"
        comment = (
            f"UNIT TESTS\n{branch_info}\n\nSTATUS: FAILED"
            f"\nLOG: {args.fail_review}\nCOMMIT: {args.commit}"
        )
        proposal.createComment(subject=subject, content=comment, vote="Needs Fixing")
        proposal.lp_save()
        return 0

    if args.succeed_review:
        subject = f"Re: [UNITTESTS] {branch_info} - TESTS PASS"
        comment = f"UNIT TESTS\n{branch_info}\n\nSTATUS: SUCCESS\nCOMMIT: {args.commit}"
        proposal.createComment(subject=subject, content=comment, vote="Approve")
        proposal.lp_save()
        return 0

    if args.start_merge:
        subject = f"Re: [Merge] {branch_info} - LANDING STARTED"
        comment = f"LANDING\n{branch_info}\n\nSTATUS: QUEUED\nLOG: {args.start_merge}"
        proposal.createComment(subject=subject, content=comment)
        return 0

    if args.fail_merge:
        subject = f"Re: [Merge] {branch_info} - LANDING FAILED"
        comment = (
            f"LANDING\n{branch_info}\n\nSTATUS: FAILED BUILD\nLOG: {args.fail_merge}"
        )
        proposal.createComment(subject=subject, content=comment)
        proposal.setStatus(status="Needs review")
        proposal.lp_save()
        return 0

    if args.succeed_merge:
        proposal.setStatus(status="Merged")
        proposal.lp_save()
        mark_bugs_fix_committed(proposal)
        return 0

    return 1


def handle_merge(args, lp):
    work_dir = args.work_dir if args.work_dir else tempfile.mkdtemp()
    # allow specifying the same target to just run tests on a branch
    same_target = (
        (args.repo_dest, args.branch_dest) == (args.repo_src, args.branch_src)
    ) or (not args.repo_src and not args.branch_src)

    os.chdir(work_dir)
    git("clone", args.repo_dest, ".", "--branch", args.branch_dest)
    if same_target:
        return
    git("remote", "add", "source", args.repo_src)
    git("fetch", "source")
    name = args.author_name
    email = args.author_email
    git("config", "user.name", name)
    git("config", "user.email", email)
    git("merge", "--squash", f"source/{args.branch_src}")
    if args.commit_msg_file:
        commit_msg = args.commit_msg_file.read()
    else:
        commit_msg = "Merge into destination for testing the build."
    git("commit", "-a", "-m", commit_msg)
    if args.push:
        git("push", "origin", f"HEAD:{args.branch_dest}")


def handle_stale_mp(args, lp):
    stale_mps = []
    maas_project = lp.project_groups.search(text="maas-project")[0]
    for mp in maas_project.getMergeProposals(status="Needs review"):
        votes = mp.votes.entries
        for vote in votes:
            if (
                vote["reviewer_link"]
                in [
                    "https://api.launchpad.net/devel/~maas-maintainers",
                    "https://api.launchpad.net/devel/~maas-committers",
                ]
                and vote["is_pending"]
            ):
                stale_time = datetime.now().date() - mp.date_review_requested.date()
                if stale_time.days >= 7:
                    stale_mps.append((stale_time.days, mp.web_link))

    stale_mps.sort(key=lambda tup: tup[0], reverse=True)
    for mp in stale_mps:
        print(f"{mp[0]} days - {mp[1]}")

    return 1 if stale_mps else 0


def main():
    parser = argparse.ArgumentParser(description="Communicate with Launchpad.")
    parser.add_argument("--credentials", help="Credentials file to login to launchpad.")

    subcommands = parser.add_subparsers(help="sub-command help")
    reviewable_jobs_parser = subcommands.add_parser("reviewable-jobs")
    reviewable_jobs_parser.add_argument(
        "jobs_cfg_dir",
        type=Path,
        help=(
            "Path to directory containing yaml launchpad-ci configs"
            " listing the repos to look for open MPs in."
        ),
    )
    reviewable_jobs_parser.set_defaults(func=handle_reviewable_jobs)

    mergeable_jobs_parser = subcommands.add_parser("mergeable-jobs")
    mergeable_jobs_parser.add_argument(
        "jobs_cfg_dir",
        type=Path,
        help=(
            "Path to directory containing yaml launchpad-ci configs"
            " listing the repos to look for open MPs in."
        ),
    )
    mergeable_jobs_parser.set_defaults(func=handle_mergable_jobs)

    mark_mp_parser = subcommands.add_parser("mark-mp")
    mark_mp_parser.add_argument(
        "--start-review",
        action="store_true",
        default=False,
        help="Mark the merge proposal that unit testing has started.",
    )
    mark_mp_parser.add_argument(
        "--fail-review", help="Mark the merge proposal as failed unit testing."
    )
    mark_mp_parser.add_argument(
        "--succeed-review",
        action="store_true",
        default=False,
        help="Mark the merge proposal as passed testing.",
    )
    mark_mp_parser.add_argument(
        "--start-merge",
        action="store_true",
        default=False,
        help="Mark the merge proposal that unit testing has started.",
    )
    mark_mp_parser.add_argument(
        "--fail-merge", help="Mark the merge proposal as failed unit testing."
    )
    mark_mp_parser.add_argument(
        "--succeed-merge",
        action="store_true",
        default=False,
        help="Mark the merge proposal as passed testing.",
    )
    mark_mp_parser.add_argument(
        "--repo-src", help="Source repository of the merge proposal."
    )
    mark_mp_parser.add_argument(
        "--branch-src", help="Source branch of the merge proposal."
    )
    mark_mp_parser.add_argument(
        "--repo-dest", help="Destination repository of the merge proposal."
    )
    mark_mp_parser.add_argument(
        "--branch-dest", help="Destination branch of the merge proposal."
    )
    mark_mp_parser.add_argument("--commit", help="SHA1 commit hash that was tested.")
    mark_mp_parser.set_defaults(func=handle_mark_mp)

    merge_parser = subcommands.add_parser("merge")
    merge_parser.add_argument(
        "--work-dir", help="Directory where the merge will take place"
    )
    merge_parser.add_argument(
        "--repo-src", help="Source repository of the merge proposal."
    )
    merge_parser.add_argument(
        "--branch-src", help="Source branch of the merge proposal."
    )
    merge_parser.add_argument(
        "--repo-dest", help="Destination repository of the merge proposal."
    )
    merge_parser.add_argument(
        "--branch-dest", help="Destination branch of the merge proposal."
    )
    merge_parser.add_argument(
        "--commit-msg-file", help="Commit message file.", type=argparse.FileType("r")
    )
    merge_parser.add_argument(
        "--push",
        help="Whether to push the commits.",
        action="store_true",
        default=False,
    )
    merge_parser.add_argument(
        "--author-name", help="Author name for the commit", required=True
    )
    merge_parser.add_argument(
        "--author-email", help="Author e-mail for the commit", required=True
    )
    merge_parser.set_defaults(func=handle_merge)

    stale_mp_parser = subcommands.add_parser("check-stale")
    stale_mp_parser.set_defaults(func=handle_stale_mp)

    args = parser.parse_args()
    lp = login_to_lp(args.credentials)
    return args.func(args, lp)


if __name__ == "__main__":
    sys.exit(main())
