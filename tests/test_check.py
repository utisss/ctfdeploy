from datetime import UTC, datetime

import pytest

from ctfdeploy.check import check, connection_port
from ctfdeploy.model import RepoError, load_repo

BETWEEN = datetime(2026, 9, 25, tzinfo=UTC)


def messages(repo, now=BETWEEN) -> list[str]:
    problems = check(load_repo(repo.root), now)
    return [f"{p.path.relative_to(repo.root)}:{p.line}: {p.message}" for p in problems]


def test_clean_repo_has_no_problems(repo):
    repo.challenge("oct/web-a", port=7001)
    repo.challenge("oct/rev-b")
    assert messages(repo) == []


def test_built_service_needs_tagged_image(repo):
    repo.challenge(
        "oct/web-a",
        compose="""\
        services:
          web:
            build: .
            ports: ["7001:80"]
        """,
    )
    assert messages(repo) == [
        "oct/web-a/docker-compose.yml:3: web: built services need image: <name>:${TAG:-dev}"
    ]


def test_swarm_dropped_and_unsafe_keys(repo):
    repo.challenge(
        "oct/web-a",
        compose="""\
        services:
          web:
            image: nginx
            restart: always
            privileged: true
            volumes:
              - ./data:/data
              - ../../secrets:/secrets
              - type: tmpfs
                target: /tmp
        """,
    )
    assert messages(repo) == [
        "oct/web-a/docker-compose.yml:4: web: restart: swarm ignores it; use deploy.restart_policy",
        "oct/web-a/docker-compose.yml:5: web: privileged is not allowed",
        "oct/web-a/docker-compose.yml:8: web: bind mounts must stay inside the challenge directory",
    ]


def test_ports_must_be_fixed_and_not_the_hosts(repo):
    repo.challenge(
        "oct/web-a",
        compose="""\
        services:
          web:
            image: nginx
            ports:
              - "80"
              - 22:22
        """,
    )
    assert messages(repo) == [
        "oct/web-a/docker-compose.yml:5: web: publish a fixed host port, e.g. 7001:80",
        "oct/web-a/docker-compose.yml:6: web: port 22 belongs to the host",
    ]


def test_port_clash_within_and_across_overlapping_events(repo):
    repo.challenge("sept/web-old", port=7001)
    repo.challenge("oct/web-a", port=7001)
    repo.challenge("oct/web-b", port=7002)
    repo.challenge("oct/web-c", port=7002)
    assert messages(repo) == [
        "oct/web-c/docker-compose.yml:6: port 7002 is also used by oct/web-b",
        "oct/web-a/docker-compose.yml:6: port 7001 is also used by sept/web-old",
    ]


def test_reused_challenge_keeps_its_port(repo):
    repo.challenge("sept/web-a", port=7001)
    repo.challenge("oct/web-a", port=7001)
    assert messages(repo) == []


def test_past_events_are_not_checked(repo):
    repo.challenge("sept/web-old", spec="name: x\n")
    assert messages(repo, now=datetime(2026, 10, 5, tzinfo=UTC)) == []


def test_challenge_spec_rules(repo):
    repo.challenge(
        "oct/web-a",
        spec="""\
        name: A
        category: Crypto
        description: d
        value: 100
        flags: ['utflag{x}']
        files: [missing.txt]
        state: hidden
        next: Nope
        """,
    )
    assert messages(repo) == [
        "oct/web-a/challenge.yml:7: remove state: "
        "ctfdeploy shows challenges when their event starts",
        "oct/web-a/challenge.yml:2: category must be one of Web, Binary Exploitation",
        "oct/web-a/challenge.yml:6: file missing.txt does not exist",
        "oct/web-a/challenge.yml:8: no challenge named Nope",
    ]


def test_connection_info_port_must_be_published(repo):
    repo.challenge(
        "oct/web-a",
        port=7001,
        spec="""\
        name: A
        category: Web
        description: d
        value: 100
        flags: ['utflag{x}']
        connection_info: http://ctf.isss.io:7002
        """,
    )
    assert messages(repo) == [
        "oct/web-a/challenge.yml:6: connection_info port 7002 is not published"
    ]


def test_duplicate_names_in_an_event(repo):
    repo.challenge("oct/web-a", name="Same")
    repo.challenge("oct/web-b", name="Same")
    assert messages(repo) == ["oct/web-b/challenge.yml:1: oct/web-a has the same name"]


def test_invalid_ctfs_yml_is_fatal_with_its_line(repo):
    repo.write(
        "ctfs.yml",
        """\
        host: ctf.isss.io
        events:
          - name: Oct
            start: 2026-10-02 18:00
            challenges: oct
        """,
    )
    with pytest.raises(RepoError) as e:
        load_repo(repo.root)
    assert e.value.line == 4


@pytest.mark.parametrize(
    ("info", "port"),
    [
        ("nc ctf.isss.io 7001", 7001),
        ("https://ctf.isss.io:8853", 8853),
        ("http://ctf.isss.io:4928/q?t=2026-09-13T06:00:00Z", 4928),
        ("https://ctf.isss.io/", None),
        (None, None),
    ],
)
def test_connection_port(info, port):
    assert connection_port(info) == port
