import pytest

from ctfdeploy.sync import _stored_name


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("ACL!!!.jpg", "ACL.jpg"),
        ("my file (1).zip", "my_file_1.zip"),
        ("café.txt", "cafe.txt"),
        (".hidden", "hidden"),
        ("plain-name_1.tar.gz", "plain-name_1.tar.gz"),
    ],
)
def test_stored_name_matches_ctfd(name, stored):
    assert _stored_name(name) == stored
