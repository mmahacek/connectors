#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Tests the Network Drive source class methods."""

import asyncio
import csv
import datetime
from unittest import mock
from unittest.mock import ANY, MagicMock

import pytest
import smbclient
from smbprotocol.exceptions import (
    SMBConnectionClosed,
    SMBOSError,
    SMBResponseException,
    Unsuccessful,
)

from connectors.access_control import ACCESS_CONTROL
from connectors.filtering.validation import SyncRuleValidationResult
from connectors.protocol import Filter
from connectors.source import ConfigurableFieldValueError
from connectors.sources.network_drive import (
    ClientPermissionException,
    InvalidLogonHoursException,
    NASDataSource,
    NetworkDriveAdvancedRulesValidator,
    NoLogonServerException,
    PasswordChangeRequiredException,
    SecurityInfo,
    SMBSession,
    UserAccountDisabledException,
)
from tests.commons import AsyncIterator
from tests.sources.support import create_source

READ_COUNT = 0
MAX_CHUNK_SIZE = 65536
ADVANCED_SNIPPET = "advanced_snippet"

WINDOWS = "windows"
LINUX = "linux"


class InvalidLogonHours(SMBResponseException):
    _STATUS_CODE = 3221225583


class UserAccountDisabled(SMBResponseException):
    _STATUS_CODE = 3221225586


class InvalidWorkstation(SMBResponseException):
    _STATUS_CODE = 3221225584


class NoLogonServer(SMBResponseException):
    _STATUS_CODE = 3221225566


class PasswordMustChange(SMBResponseException):
    _STATUS_CODE = 3221226020


def mock_file(name):
    """Generates the smbprotocol object for a file

    Args:
        name (str): The name of the mocked file
    """
    mock_response = mock.Mock()
    mock_response.name = name
    mock_response.path = f"\\1.2.3.4/dummy_path/{name}"
    mock_stats = {}
    mock_stats["file_id"] = mock.Mock()
    mock_stats["file_id"].get_value.return_value = "1"
    mock_stats["end_of_file"] = mock.Mock()
    mock_stats["end_of_file"].get_value.return_value = "30"
    mock_stats["creation_time"] = mock.Mock()
    mock_stats["creation_time"].get_value.return_value = datetime.datetime(
        2022, 1, 11, 12, 12, 30
    )
    mock_stats["change_time"] = mock.Mock()
    mock_stats["change_time"].get_value.return_value = datetime.datetime(
        2022, 4, 21, 12, 12, 30
    )

    mock_response._dir_info.fields = mock_stats
    mock_response.is_dir.return_value = False

    return mock_response


def mock_folder(name):
    """Generates the smbprotocol object for a folder

    Args:
        name (str): The name of the mocked folder
    """
    mock_response = mock.Mock()
    mock_response.name = name
    mock_response.path = f"\\1.2.3.4/dummy_path/{name}"
    mock_response.is_dir.return_value = True
    mock_stats = {}
    mock_stats["file_id"] = mock.Mock()
    mock_stats["file_id"].get_value.return_value = "122"
    mock_stats["end_of_file"] = mock.Mock()
    mock_stats["end_of_file"].get_value.return_value = "200"
    mock_stats["creation_time"] = mock.Mock()
    mock_stats["creation_time"].get_value.return_value = datetime.datetime(
        2022, 2, 11, 12, 12, 30
    )
    mock_stats["change_time"] = mock.Mock()
    mock_stats["change_time"].get_value.return_value = datetime.datetime(
        2022, 5, 21, 12, 12, 30
    )
    mock_response._dir_info.fields = mock_stats
    return mock_response


def side_effect_function(MAX_CHUNK_SIZE):
    """Dynamically changing return values during reading a file in chunks
    Args:
        MAX_CHUNK_SIZE: Maximum bytes allowed to be read at a given time
    """
    global READ_COUNT
    if READ_COUNT:
        return None
    READ_COUNT += 1
    return b"Mock...."


def mock_permission(sid, ace):
    mock_response = {}

    class MockSID:
        def __str__(self):
            return sid

    mock_response["ace_type"] = mock.Mock()
    mock_response["ace_type"].value = ace

    mock_response["mask"] = mock.Mock()
    mock_response["mask"].value = 234

    mock_response["sid"] = MockSID()

    return mock_response


@pytest.mark.asyncio
async def test_ping_for_successful_connection():
    """Tests the ping functionality for ensuring connection to the Network Drive."""
    # Setup
    expected_response = True
    response = asyncio.Future()
    response.set_result(expected_response)

    # Execute
    with mock.patch.object(smbclient, "register_session", return_value=response):
        async with create_source(NASDataSource) as source:
            await source.ping()


@pytest.mark.asyncio
@mock.patch("smbclient.register_session")
async def test_ping_for_failed_connection(session_mock):
    """Tests the ping functionality when connection can not be established to Network Drive.

    Args:
        session_mock (patch): The patch of register_session method
    """
    # Setup
    response = asyncio.Future()
    response.set_result(None)
    session_mock.side_effect = ValueError
    async with create_source(NASDataSource) as source:
        # Execute
        source.smb_connection.session = None
        with pytest.raises(Exception):
            await source.ping()


@pytest.mark.asyncio
@mock.patch("smbclient.register_session")
@pytest.mark.parametrize(
    "side_effect",
    [
        {
            "side_effect_name": InvalidWorkstation,
            "raises": ClientPermissionException,
        },
        {
            "side_effect_name": PasswordMustChange,
            "raises": PasswordChangeRequiredException,
        },
        {
            "side_effect_name": NoLogonServer,
            "raises": NoLogonServerException,
        },
        {
            "side_effect_name": UserAccountDisabled,
            "raises": UserAccountDisabledException,
        },
        {
            "side_effect_name": InvalidLogonHours,
            "raises": InvalidLogonHoursException,
        },
        {
            "side_effect_name": Unsuccessful,
            "raises": SMBResponseException,
        },
    ],
)
async def test_create_connection_when_error_occurs(session_mock, side_effect):
    """Tests the create_connection function when an error occurs

    Args:
        session_mock (patch): The patch of register_session method
    """
    # Setup
    async with create_source(NASDataSource) as source:
        session_mock.side_effect = side_effect.get("side_effect_name")

        # Execute
        with pytest.raises(
            side_effect.get("raises"),
        ):
            source.smb_connection.create_connection()


@mock.patch("smbclient.scandir")
@pytest.mark.asyncio
async def test_traverse_diretory_with_invalid_path(dir_mock):
    """Tests the scandir method of smbclient throws error on invalid path

    Args:
        dir_mock (patch): The patch of scandir method
    """
    # Setup
    async with create_source(NASDataSource) as source:
        path = "unknown_path"
        dir_mock.side_effect = SMBOSError(ntstatus=3221225487, filename="unknown_path")

        # Execute
        async for file in source.traverse_diretory(path=path):
            assert file == []


@mock.patch("smbclient.scandir")
@mock.patch("connectors.utils.time_to_sleep_between_retries", mock.Mock(return_value=0))
@pytest.mark.asyncio
async def test_traverse_diretory_retried_on_smb_timeout(dir_mock):
    """Tests the scandir method of smbclient is retried on SMBConnectionClosed error

    Args:
        dir_mock (patch): The patch of scandir method
    """
    with mock.patch.object(SMBSession, "create_connection"):
        async with create_source(NASDataSource) as source:
            path = "some_path"
            dir_mock.side_effect = [
                SMBConnectionClosed,
                SMBConnectionClosed,
                [mock_file(name="a1.md")],
            ]

            expected_output = {
                "_id": "1",
                "_timestamp": "2022-04-21T12:12:30",
                "path": "\\1.2.3.4/dummy_path/a1.md",
                "title": "a1.md",
                "created_at": "2022-01-11T12:12:30",
                "size": "30",
                "type": "file",
            }
            async for file in source.traverse_diretory(path=path):
                assert file == expected_output


@pytest.mark.asyncio
@mock.patch("smbclient.open_file")
async def test_fetch_file_when_file_is_inaccessible(file_mock, caplog):
    """Tests the open_file method of smbclient throws error when file cannot be accessed

    Args:
        file_mock (patch): The patch of open_file method
    """
    # Setup
    async with create_source(NASDataSource) as source:
        caplog.set_level("ERROR")
        path = "\\1.2.3.4/Users/file1.txt"
        file_mock.side_effect = SMBOSError(ntstatus=0xC0000043, filename="file1.txt")

        # Execute
        async for _response in source.fetch_file_content(path=path):
            # Assert
            assert (
                "Cannot read the contents of file on path:\1.2.3.4/Users/file1.txt. Error [Error 1] [NtStatus 0xc0000043] The process cannot access the file because it is being used by another process: 'file1.txt'"
                in caplog.text
            )


async def create_fake_coroutine(data):
    """create a method for returning fake coroutine value"""
    return data


@pytest.mark.asyncio
async def test_get_content():
    """Test get_content method of Network Drive"""
    # Setup
    async with create_source(NASDataSource) as source:
        mock_response = {
            "id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "title": "file1.txt",
            "path": "\\1.2.3.4/Users/folder1/file1.txt",
            "size": 50,
        }

        expected_output = {
            "_id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "_attachment": "TW9jay4uLi4=",
        }

        # Execute
        source.download_and_extract_file = mock.MagicMock(
            return_value=create_fake_coroutine(expected_output)
        )
        actual_response = await source.get_content(mock_response, doit=True)

        # Assert
        assert actual_response == expected_output


@pytest.mark.asyncio
async def test_get_content_with_upper_extension():
    """Test get_content method of Network Drive"""
    # Setup
    async with create_source(NASDataSource) as source:
        mock_response = {
            "id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "title": "file1.TXT",
            "path": "\\1.2.3.4/Users/folder1/file1.txt",
            "size": 50,
        }

        expected_output = {
            "_id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "_attachment": "TW9jay4uLi4=",
        }

        # Execute
        source.download_and_extract_file = mock.MagicMock(
            return_value=create_fake_coroutine(expected_output)
        )
        actual_response = await source.get_content(mock_response, doit=True)

        # Assert
        assert actual_response == expected_output


@pytest.mark.asyncio
async def test_get_content_when_doit_false():
    """Test get_content method when doit is false."""
    # Setup
    async with create_source(NASDataSource) as source:
        mock_response = {
            "id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "title": "file1.txt",
        }

        # Execute
        actual_response = await source.get_content(mock_response)

        # Assert
        assert actual_response is None


@pytest.mark.asyncio
async def test_get_content_when_file_size_is_large():
    """Test the module responsible for fetching the content of the file if it is not extractable"""
    # Setup
    async with create_source(NASDataSource) as source:
        mock_response = {
            "id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "title": "file1.txt",
            "size": 20000000000,
        }

        # Execute
        actual_response = await source.get_content(mock_response, doit=True)

        # Assert
        assert actual_response is None


@pytest.mark.asyncio
async def test_get_content_when_file_type_not_supported():
    """Test get_content method when the file content type is not supported"""
    # Setup
    async with create_source(NASDataSource) as source:
        mock_response = {
            "id": "1",
            "_timestamp": "2022-04-21T12:12:30",
            "title": "file2.dmg",
            "size": 123,
        }

        # Execute
        actual_response = await source.get_content(mock_response, doit=True)

        # Assert
        assert actual_response is None


@pytest.mark.asyncio
@mock.patch.object(NASDataSource, "traverse_diretory", return_value=mock.MagicMock())
@mock.patch.object(NASDataSource, "fetch_groups_info", return_value=mock.AsyncMock())
@mock.patch("smbclient.register_session")
async def test_get_doc(mock_traverse_diretory, mock_fetch_groups, session):
    # Setup
    async with create_source(NASDataSource) as source:
        # Execute
        async for _, _ in source.get_docs():
            # Assert
            mock_traverse_diretory.assert_awaited()


@pytest.mark.asyncio
@mock.patch("smbclient.open_file")
async def test_fetch_file_when_file_is_accessible(file_mock):
    """Tests the open_file method of smbclient when file can be accessed

    Args:
        file_mock (patch): The patch of open_file method
    """
    # Setup
    async with create_source(NASDataSource) as source:
        path = "\\1.2.3.4/Users/file1.txt"

        file_mock.return_value.__enter__.return_value.read = mock.MagicMock(
            side_effect=side_effect_function
        )

        # Execute
        async for response in source.fetch_file_content(path=path):
            # Assert
            assert response in [b"Mock....", b""]


@pytest.mark.asyncio
async def test_close_without_session():
    async with create_source(NASDataSource) as source:
        await source.close()

    assert not hasattr(source.smb_connection.__dict__, "session")


@pytest.mark.parametrize(
    "advanced_rules, expected_validation_result, mocked_document",
    [
        (
            # valid: empty array should be valid
            [],
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
            [],
        ),
        (
            # valid: empty object should also be valid -> default value in Kibana
            {},
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
            [],
        ),
        (
            # valid: one custom pattern
            [{"pattern": "a/b"}],
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
            [
                AsyncIterator(
                    [
                        {
                            "path": "\\\1.2.3.4/a/b",
                            "size": "0",
                            "_id": "0",
                            "created_at": "1111-11-11T11:11:11",
                            "type": "folder",
                            "title": "b",
                            "_timestamp": "1212-12-12T12:12:12",
                        },
                    ]
                )
            ],
        ),
        (
            # valid: two custom patterns
            [{"pattern": "a/b/*"}, {"pattern": "a/**"}],
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
            [
                AsyncIterator(
                    [
                        {
                            "path": "\\1.2.3.4/a/b/c",
                            "size": "0",
                            "_id": "1",
                            "created_at": "1111-11-11T11:11:11",
                            "type": "folder",
                            "title": "c",
                            "_timestamp": "1212-12-12T12:12:12",
                        },
                    ]
                ),
                AsyncIterator(
                    [
                        {
                            "path": "\\1.2.3.4/a/b/e",
                            "size": "0",
                            "_id": "3",
                            "created_at": "1111-11-11T11:11:11",
                            "type": "folder",
                            "title": "e",
                            "_timestamp": "1212-12-12T12:12:12",
                        },
                    ]
                ),
            ],
        ),
        (
            # invalid: pattern empty
            [{"pattern": "a/**/c"}, {"pattern": ""}],
            SyncRuleValidationResult(
                SyncRuleValidationResult.ADVANCED_RULES,
                is_valid=False,
                validation_message=ANY,
            ),
            [],
        ),
        (
            # invalid: unallowed key
            [{"pattern": "a/b/*"}, {"queries": "a/d"}],
            SyncRuleValidationResult(
                SyncRuleValidationResult.ADVANCED_RULES,
                is_valid=False,
                validation_message=ANY,
            ),
            [],
        ),
        (
            # invalid: list of strings -> wrong type
            {"pattern": ["a/b/*"]},
            SyncRuleValidationResult(
                SyncRuleValidationResult.ADVANCED_RULES,
                is_valid=False,
                validation_message=ANY,
            ),
            [],
        ),
        (
            # invalid: array of arrays -> wrong type
            {"path": ["a/b/c", ""]},
            SyncRuleValidationResult(
                SyncRuleValidationResult.ADVANCED_RULES,
                is_valid=False,
                validation_message=ANY,
            ),
            [],
        ),
    ],
)
@pytest.mark.asyncio
async def test_advanced_rules_validation(
    advanced_rules, expected_validation_result, mocked_document
):
    async with create_source(NASDataSource) as source:
        validation_result = await NetworkDriveAdvancedRulesValidator(source).validate(
            advanced_rules
        )

        assert validation_result == expected_validation_result


@pytest.mark.asyncio
async def test_pattern_start_with_slash():
    async with create_source(NASDataSource) as source:
        expected_result = await NetworkDriveAdvancedRulesValidator(source).validate(
            advanced_rules=[{"pattern": "/[abc"}]
        )
        assert expected_result == SyncRuleValidationResult(
            SyncRuleValidationResult.ADVANCED_RULES,
            is_valid=False,
            validation_message=ANY,
        )


class MockFile:
    def __init__(self, path):
        self.path = path

    def is_dir(self):
        return False

    def path(self):
        return self.path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filtering",
    [
        Filter(
            {
                ADVANCED_SNIPPET: {
                    "value": [
                        {"pattern": "training/python/**"},
                        {"pattern": "training/**"},
                    ]
                }
            }
        ),
    ],
)
@mock.patch("smbclient.register_session")
async def test_get_docs_for_syncrule(session, filtering):
    async with create_source(NASDataSource) as source:
        with mock.patch(
            "smbclient.scandir",
            return_value=[MockFile("\\1.2.3.4/training/python/training.py")],
        ):
            source.format_document = MagicMock(
                return_value={
                    "_id": "1",
                    "_timestamp": "2022-04-21T12:12:30",
                    "path": "\\1.2.3.4/training/python/training.py",
                    "title": "training.py",
                    "created_at": "2022-01-11T12:12:30",
                    "size": "30",
                    "type": "file",
                }
            )
            async for document, _ in source.get_docs(filtering):
                assert document is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filtering",
    [
        Filter({ADVANCED_SNIPPET: {"value": [{"pattern": "training/python/async*"}]}}),
    ],
)
@mock.patch("smbclient.register_session")
async def test_get_docs_for_syncrule_negative(session, filtering):
    async with create_source(NASDataSource) as source:
        with mock.patch(
            "smbclient.scandir",
            return_value=[MockFile("\\1.2.3.4/training/python/training.py")],
        ):
            async for document in source.get_docs(filtering):
                assert document is not None


def test_parse_output():
    security_object = SecurityInfo("user", "password", "0.0.0.0")

    raw_output = mock.Mock()
    raw_output.std_out.decode.return_value = """
            Header1  Value1
            Header2  Value2
            Key 1    Value1
            Key 2    Value2
            Key 3    Value3
        """

    formatted_result = security_object.parse_output(raw_output)

    expected_result = {"Key 1": "Value1", "Key 2": "Value2", "Key 3": "Value3"}

    assert formatted_result == expected_result


def test_fetch_users():
    security_object = SecurityInfo("user", "password", "0.0.0.0")

    sample_data = mock.Mock()
    sample_data.std_out.decode.return_value = """
            Header  Value
            ======  =====
            User A  S-1-11-111
            User B  S-2-22-222
            User C  S-3-33-333
        """
    security_object.session.run_ps = mock.Mock(return_value=sample_data)

    users = security_object.fetch_users()
    expected_result = {
        "User A": "S-1-11-111",
        "User B": "S-2-22-222",
        "User C": "S-3-33-333",
    }

    assert users == expected_result


def test_fetch_groups():
    security_object = SecurityInfo("user", "password", "0.0.0.0")

    sample_data = mock.Mock()
    sample_data.std_out.decode.return_value = """
            Header  Value
            ======  =====
            Group 1  S-1-11-111-2222
            Group 2  S-2-22-222-3333
            Group 3  S-3-33-333-4444
        """
    security_object.session.run_ps = mock.Mock(return_value=sample_data)

    users = security_object.fetch_groups()
    expected_result = {
        "Group 1": "S-1-11-111-2222",
        "Group 2": "S-2-22-222-3333",
        "Group 3": "S-3-33-333-4444",
    }

    assert users == expected_result


def test_fetch_members():
    security_object = SecurityInfo("user", "password", "0.0.0.0")

    sample_data = mock.Mock()
    sample_data.std_out.decode.return_value = """
            Header  Value
            ======  =====
            User 1  S-1-11-111
            User 2  S-2-22-222
            User 3  S-3-33-333
        """
    security_object.session.run_ps = mock.Mock(return_value=sample_data)

    users = security_object.fetch_members(group_name="abc")
    expected_result = {
        "User 1": "S-1-11-111",
        "User 2": "S-2-22-222",
        "User 3": "S-3-33-333",
    }

    assert users == expected_result


@pytest.mark.asyncio
async def test_get_access_control_dls_disabled():
    async with create_source(NASDataSource) as source:
        source._features = mock.Mock()
        source._features.document_level_security_enabled = MagicMock(return_value=False)

        acl = []
        async for access_control in source.get_access_control():
            acl.append(access_control)

        assert len(acl) == 0


@pytest.mark.asyncio
async def test_get_access_control_linux_empty_csv_file_path():
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)
        source.drive_type = LINUX
        source.identity_mappings = ""
        with pytest.raises(ConfigurableFieldValueError):
            await anext(source.get_access_control())


@pytest.mark.asyncio
async def test_get_access_control_linux():
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)
        source.drive_type = LINUX
        source.identity_mappings = "/a/b"
        with mock.patch(
            "builtins.open",
            mock.mock_open(read_data="user1;S-1;S-11,S-22\nuser2;S-2;S-22"),
        ):
            acl = []
            async for access_control in source.get_access_control():
                acl.append(access_control)
            assert len(acl) == 2


@pytest.mark.asyncio
async def test_fetch_groups_info():
    mock_groups = {"Admins": "S-1-5-32-546"}
    mock_group_members = {
        "Administrator": "S-1-5-21-227823342-1368486282-703244805-500"
    }
    excepted_result = {
        "546": {"Administrator": "S-1-5-21-227823342-1368486282-703244805-500"}
    }
    async with create_source(NASDataSource) as source:
        with mock.patch.object(SecurityInfo, "fetch_groups", return_value=mock_groups):
            with mock.patch.object(
                SecurityInfo, "fetch_members", return_value=mock_group_members
            ):
                groups_info = await source.fetch_groups_info()
        assert groups_info == excepted_result


@pytest.mark.asyncio
async def test_get_access_control_dls_enabled():
    expected_user_access_control = [
        [
            "rid:500",
            "user:Administrator",
        ],
        [
            "rid:501",
            "user:Guest",
        ],
    ]

    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)
        source.drive_type = WINDOWS

        mock_users = {
            "Administrator": "S-1-5-21-227823342-1368486282-703244805-500",
            "Guest": "S-1-5-21-227823342-1368486282-703244805-501",
        }

        with mock.patch.object(SecurityInfo, "fetch_users", return_value=mock_users):
            user_access_control = []
            async for user_doc in source.get_access_control():
                user_doc["query"]["template"]["params"]["access_control"].sort()
                user_access_control.append(
                    user_doc["query"]["template"]["params"]["access_control"]
                )

        assert expected_user_access_control == user_access_control


@pytest.mark.asyncio
async def test_get_access_control_without_duplicate_ids():
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)
        source.drive_type = LINUX
        source.identity_mappings = "/a/b"

        source.read_user_info_csv = MagicMock(
            return_value=[
                {"name": "user1", "user_sid": "S-1", "groups": ["S-11", "S-22"]},
                {"name": "user2", "user_sid": "S-2", "groups": ["S-22"]},
                {"name": "user3", "user_sid": "S-3", "groups": ["S-11"]},
            ]
        )

        seen_users = set()
        async for access_control in source.get_access_control():
            seen_users.add(access_control["_id"])

        assert len(seen_users) == 3


@mock.patch.object(
    NASDataSource,
    "traverse_diretory",
    side_effect=[
        AsyncIterator(
            [
                {
                    "_id": "1",
                    "_timestamp": "2022-04-21T12:12:30",
                    "path": "\\1.2.3.4/a/a1.txt",
                    "title": "a1.txt",
                    "created_at": "2022-01-11T12:12:30",
                    "size": "30",
                    "type": "file",
                },
                {
                    "_id": "122",
                    "_timestamp": "2022-05-21T12:12:30",
                    "path": "\\1.2.3.4/a/A",
                    "title": "A",
                    "created_at": "2022-02-11T12:12:30",
                    "size": "200",
                    "type": "folder",
                },
            ]
        ),
    ],
)
@mock.patch.object(NASDataSource, "fetch_groups_info", return_value=mock.AsyncMock())
@mock.patch("smbclient.register_session")
@pytest.mark.asyncio
async def test_get_docs_without_dls_enabled(mock_get_files, mock_fetch_groups, session):
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=False)

        # Execute
        documents, downloads = [], []
        async for item, content in source.get_docs():
            documents.append(item)

            if content:
                downloads.append(content)

        assert len(documents) == 2

        assert len(downloads) == 1


@pytest.mark.asyncio
@mock.patch("smbclient.register_session")
@mock.patch.object(
    NASDataSource,
    "traverse_diretory",
    side_effect=[
        AsyncIterator(
            [
                {
                    "_id": "1",
                    "_timestamp": "2022-04-21T12:12:30",
                    "path": "\\1.2.3.4/a/a1.txt",
                    "title": "a1.txt",
                    "created_at": "2022-01-11T12:12:30",
                    "size": "30",
                    "type": "file",
                },
                {
                    "_id": "122",
                    "_timestamp": "2022-05-21T12:12:30",
                    "path": "\\1.2.3.4/a/A",
                    "title": "A",
                    "created_at": "2022-02-11T12:12:30",
                    "size": "200",
                    "type": "folder",
                },
            ]
        ),
    ],
)
@mock.patch.object(
    NASDataSource,
    "list_file_permission",
    side_effect=[
        [mock_permission("S-2-21-211-2112", 0), mock_permission("S-1-11-111-1111", 0)],
        [mock_permission("S-3-31-131-1131", 0), mock_permission("S-1-11-111-1111", 0)],
    ],
)
@mock.patch.object(
    SecurityInfo,
    "fetch_groups",
    return_value={"Admins": "S-1-5-32-546", "IT": "S-1-12-222-4456"},
)
@mock.patch.object(
    SecurityInfo,
    "fetch_members",
    side_effect=[{"User1": "S-1-11-111-1111"}, {"User2": "S-2-21-211-2111"}],
)
@mock.patch.object(
    SecurityInfo,
    "fetch_users",
    return_value={
        "User1": "S-1-11-111-1111",
        "User2": "S-2-21-211-2111",
        "User3": "S-3-31-311-3111",
    },
)
async def test_get_docs_with_dls_enabled(
    session,
    mock_traverse_diretory,
    mock_permissions,
    mock_groups,
    mock_members,
    mock_users,
):
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)

        # Execute
        documents, downloads = [], []
        async for item, content in source.get_docs():
            documents.append(item)

            if content:
                downloads.append(content)

        assert len(documents) == 2

        assert len(downloads) == 1


@pytest.mark.asyncio
async def test_read_csv_with_valid_data():
    async with create_source(NASDataSource) as source:
        with mock.patch(
            "builtins.open",
            mock.mock_open(read_data="user1;S-1;S-11,S-22\nuser2;S-2;S-22"),
        ):
            user_info = source.read_user_info_csv()
            expected_user_info = [
                {"name": "user1", "user_sid": "S-1", "groups": ["S-11", "S-22"]},
                {"name": "user2", "user_sid": "S-2", "groups": ["S-22"]},
            ]
            assert user_info == expected_user_info


@pytest.mark.asyncio
async def test_read_csv_file_erroneous():
    async with create_source(NASDataSource) as source:
        with mock.patch("builtins.open", mock.mock_open(read_data="0I`00�^")):
            with mock.patch("csv.reader", side_effect=csv.Error):
                user_info = source.read_user_info_csv()
                assert user_info == []


@pytest.mark.asyncio
async def test_read_csv_with_empty_groups():
    async with create_source(NASDataSource) as source:
        with mock.patch(
            "builtins.open", mock.mock_open(read_data="user1;1;\nuser2;2;")
        ):
            user_info = source.read_user_info_csv()
            expected_user_info = [
                {"name": "user1", "user_sid": "1", "groups": []},
                {"name": "user2", "user_sid": "2", "groups": []},
            ]
            assert user_info == expected_user_info


@pytest.mark.asyncio
@mock.patch.object(SecurityInfo, "get_descriptor")
async def test_list_file_permissions(mock_get_descriptor):
    with mock.patch("smbclient.open_file", return_value=MagicMock()) as mock_file:
        mock_file.fd.return_value = 2
        mock_descriptor = mock.Mock()
        mock_get_descriptor.return_value = mock_descriptor
        mock_dacl = {"aces": ["ace1", "ace2"]}
        mock_descriptor.get_dacl.return_value = mock_dacl

        async with create_source(NASDataSource) as source:
            result = source.list_file_permission(
                file_path="/path/to/file.txt",
                file_type="file",
                mode="rb",
                access="read",
            )

            assert result == mock_dacl["aces"]


@pytest.mark.asyncio
async def test_list_file_permissions_with_inaccessible_file():
    with mock.patch("smbclient.open_file", return_value=MagicMock()) as mock_file:
        mock_file.side_effect = SMBOSError(ntstatus=0xC0000043, filename="file1.txt")

        async with create_source(NASDataSource) as source:
            result = source.list_file_permission(
                file_path="/path/to/file.txt",
                file_type="file",
                mode="rb",
                access="read",
            )

            assert result is None


@pytest.mark.asyncio
@mock.patch.object(
    NASDataSource,
    "list_file_permission",
    return_value=[
        mock_permission(sid="S-2-21-211-411", ace=0),  # User with allow permission
        mock_permission(sid="S-1-11-10", ace=1),  # Group with Deny permission
    ],
)
async def test_deny_permission_has_precedence_over_allow(mock_list_file_permission):
    mock_groups_info = {"10": {"admin": "S-2-21-211-411"}}
    expected_result = []
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)
        document_permissions = await source._decorate_with_access_control(
            document={"id": "123", "title": "sample.py"},
            file_path="dummy_url/file1",
            file_type="file",
            groups_info=mock_groups_info,
        )
        assert document_permissions[ACCESS_CONTROL] == expected_result


@pytest.mark.asyncio
@mock.patch.object(
    NASDataSource,
    "list_file_permission",
    return_value=[
        mock_permission(sid="S-2-21-211-411", ace=0),  # User 1 with allow permission
        mock_permission(sid="S-3-23-222-221", ace=1),  # User 2 with deny permission
        mock_permission(sid="S-1-11-11", ace=0),  # Group with allow permission
    ],
)
async def test_group_allow_ace_member1_allow_member2_deny_ace_then_member1_has_access(
    mock_list_file_permission,
):
    mock_groups_info = {"11": {"user-1": "S-2-21-211-411", "user-2": "S-3-23-222-221"}}
    expected_result = ["rid:411"]  # Only User-1 should have access
    async with create_source(NASDataSource) as source:
        source._dls_enabled = MagicMock(return_value=True)
        document_permissions = await source._decorate_with_access_control(
            document={"id": "123", "title": "sample.py"},
            file_path="dummy_url/file1",
            file_type="file",
            groups_info=mock_groups_info,
        )
        assert document_permissions[ACCESS_CONTROL] == expected_result


async def test_validate_drive_path():
    async with create_source(NASDataSource) as source:
        source.configuration.get_field("drive_path").value = "/abc/bcd"
        source.configuration.get_field("username").value = "user"
        source.configuration.get_field("password").value = "pass"
        source.configuration.get_field("server_ip").value = "10.50.1.7"
        source.configuration.get_field("server_port").value = 447
        with pytest.raises(ConfigurableFieldValueError):
            await source.validate_config()


@mock.patch("smbclient.scandir")
@mock.patch("connectors.utils.time_to_sleep_between_retries", mock.Mock(return_value=0))
@pytest.mark.asyncio
async def test_traverse_diretory_smb_timeout_for_sync_rule(dir_mock):
    with mock.patch.object(SMBSession, "create_connection"):
        async with create_source(NASDataSource) as source:
            path = "some_path"
            dir_mock.side_effect = [
                SMBConnectionClosed,
                SMBConnectionClosed,
                [mock_file(name="a1.md")],
            ]

            expected_output = {
                "_id": "1",
                "_timestamp": "2022-04-21T12:12:30",
                "path": "\\1.2.3.4/dummy_path/a1.md",
                "title": "a1.md",
                "created_at": "2022-01-11T12:12:30",
                "size": "30",
                "type": "file",
            }
            async for file in source.traverse_directory_for_syncrule(
                path=path, glob_pattern="/a*", indexed_rules=[]
            ):
                assert file == expected_output


@mock.patch("smbclient.scandir")
@pytest.mark.asyncio
async def test_traverse_diretory_with_invalid_path_for_syncrule(dir_mock):
    # Setup
    async with create_source(NASDataSource) as source:
        path = "unknown_path"
        dir_mock.side_effect = SMBOSError(ntstatus=3221225487, filename="unknown_path")

        # Execute
        async for file in source.traverse_directory_for_syncrule(
            path=path, glob_pattern="/a*", indexed_rules=[]
        ):
            assert file == []
