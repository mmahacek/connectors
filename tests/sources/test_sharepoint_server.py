#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Tests the Sharepoint source class methods."""

import ssl
from contextlib import asynccontextmanager
from unittest import mock
from unittest.mock import Mock, patch

import httpx
import pytest
from httpx import ByteStream

from connectors.logger import logger
from connectors.source import ConfigurableFieldValueError
from connectors.sources.sharepoint_server import SharepointServerDataSource
from tests.commons import AsyncIterator
from tests.sources.support import create_source

EXCEPTION_MESSAGE = "Something went wrong"
HOST_URL = "http://127.0.0.1:8491"


@asynccontextmanager
async def create_sps_source(
    ssl_enabled=False, ssl_ca="", retry_count=3, use_text_extraction_service=False
):
    async with create_source(
        SharepointServerDataSource,
        username="admin",
        password="changeme",
        host_url=HOST_URL,
        site_collections="collection1",
        ssl_enabled=ssl_enabled,
        ssl_ca=ssl_ca,
        retry_count=retry_count,
        use_text_extraction_service=use_text_extraction_service,
    ) as source:
        yield source


class MockSSL:
    """This class contains methods which returns dummy ssl context"""

    def load_verify_locations(self, cadata):
        """This method verify locations"""
        pass


class MockResponse:
    """Mock class for ClientResponse"""

    def __init__(self, json, status):
        self._json = json
        self.status = status
        self.is_success = True

    async def json(self):
        """This Method is used to return a json response"""
        return self._json

    async def __aexit__(self, exc_type, exc, tb):
        """Closes an async with block"""
        pass

    async def __aenter__(self):
        """Enters an async with block"""
        return self


class MockObjectResponse:
    """Class to mock object response of httpx session.get method"""

    def __init__(self):
        """Setup a streamReader object"""
        self.content = ByteStream

    async def __aexit__(self, exc_type, exc, tb):
        """Closes an async with block"""
        pass

    async def __aenter__(self):
        """Enters an async with block"""
        return self


async def async_native_coroutine_generator(item):
    """create a method for returning fake async value for
    Args:
        item: Value for converting into async
    """
    yield item


@pytest.mark.asyncio
async def test_ping_for_successful_connection():
    """Tests the ping functionality for ensuring connection to the Sharepoint."""

    async with create_sps_source() as source:
        source._logger = logger
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(200)
        )

        await source.ping()


@pytest.mark.asyncio
async def test_ping_for_failed_connection_exception():
    """Tests the ping functionality when connection can not be established to Sharepoint."""

    async with create_sps_source(retry_count=0) as source:
        mock_response = {"access_token": "test2344", "expires_in": "1234555"}
        async_response = MockResponse(mock_response, 200)
        with patch.object(
            httpx, "AsyncClient", side_effect=Exception(EXCEPTION_MESSAGE)
        ):
            with patch("httpx.get", return_value=async_response):
                with pytest.raises(Exception):
                    await source.ping()


@pytest.mark.asyncio
async def test_validate_config_when_host_url_is_empty():
    """This function test validate_config when host_url is empty"""
    async with create_sps_source() as source:
        source.configuration.set_field(name="host_url", value="")

        with pytest.raises(ConfigurableFieldValueError):
            await source.validate_config()


@pytest.mark.asyncio
async def test_validate_config_for_ssl_enabled_when_ssl_ca_not_empty_does_not_raise_error():
    """This function test validate_config when ssl is enabled and certificate is missing"""
    with patch.object(ssl, "create_default_context", return_value=MockSSL()):
        async with create_sps_source(
            ssl_enabled=True,
            ssl_ca="-----BEGIN CERTIFICATE----- Certificate -----END CERTIFICATE-----",
            retry_count=1,
        ) as source:
            get_response = {
                "value": [
                    {
                        "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                        "ServerRelativeUrl": "/sites/collection1",
                        "Title": "ctest",
                    }
                ]
            }
            source.sharepoint_client._api_call = Mock(
                return_value=async_native_coroutine_generator(get_response)
            )

            await source.validate_config()


@pytest.mark.asyncio
async def test_validate_config_for_ssl_enabled_when_ssl_ca_empty_raises_error():
    async with create_sps_source(ssl_enabled=True) as source:
        with pytest.raises(ConfigurableFieldValueError):
            await source.validate_config()


@pytest.mark.asyncio
async def test_api_call_for_exception():
    """This function test _api_call when credentials are incorrect"""
    async with create_sps_source(retry_count=0) as source:
        with patch.object(
            httpx, "AsyncClient", side_effect=Exception(EXCEPTION_MESSAGE)
        ):
            with pytest.raises(Exception):
                await source.sharepoint_client._api_call(url_name="lists", url="abc")


@pytest.mark.asyncio
async def test_prepare_drive_items_doc():
    """Test the prepare drive items method"""
    async with create_sps_source() as source:
        list_items = {
            "File": {
                "Name": "dummy",
                "TimeLastModified": "2023-01-30T12:48:31Z",
                "TimeCreated": "2023-01-30T12:48:31Z",
                "Length": "12",
                "ServerRelativeUrl": "/site",
            },
            "GUID": 1,
            "item_type": "File",
            "Editor": {"Name": "system", "Id": 1},
            "Author": {"Name": "system", "Id": 1},
        }
        expected_response = {
            "_id": "b87b3146776b01cd1ab33893eefe70fe",
            "type": "File",
            "size": 12,
            "title": "dummy",
            "creation_time": "2023-01-30T12:48:31Z",
            "_timestamp": "2023-01-30T12:48:31Z",
            "url": f"{HOST_URL}/site",
            "server_relative_url": "/site",
            "author": "system",
            "editor": "system",
            "author_id": 1,
            "editor_id": 1,
        }

        target_response = source.format_drive_item(
            item=list_items, site_url=f"{HOST_URL}/site", server_relative_url="/site"
        )
        assert target_response == expected_response


@pytest.mark.asyncio
async def test_prepare_list_items_doc():
    """Test the prepare list items method"""
    async with create_sps_source() as source:
        list_items = {
            "Title": "dummy",
            "AuthorId": 123,
            "EditorId": 123,
            "Created": "2023-01-30T12:48:31Z",
            "Modified": "2023-01-30T12:48:31Z",
            "GUID": 1,
            "FileRef": "/site",
            "url": f"{HOST_URL}/site%5E",
            "file_name": "filename",
            "server_relative_url": "/site^",
            "Editor": {"Name": "system"},
            "Author": {"Name": "system"},
        }
        expected_response = {
            "type": "list_item",
            "_id": "2c797bdb5655fa7140cd48afcba26894",
            "file_name": "filename",
            "size": 0,
            "title": "dummy",
            "author": "system",
            "editor": "system",
            "author_id": 123,
            "editor_id": 123,
            "creation_time": "2023-01-30T12:48:31Z",
            "_timestamp": "2023-01-30T12:48:31Z",
            "url": f"{HOST_URL}/site%5E",
            "server_relative_url": "/site^",
        }

        target_response = source.format_list_item(
            item=list_items,
        )
        assert target_response == expected_response


@pytest.mark.asyncio
async def test_prepare_sites_doc():
    """Test the method for preparing sites document"""
    async with create_sps_source() as source:
        list_items = {
            "Title": "dummy",
            "LastItemModifiedDate": "2023-01-30T12:48:31Z",
            "Created": "2023-01-30T12:48:31Z",
            "Id": 1,
            "Url": "sharepoint.com",
            "ServerRelativeUrl": "/site",
            "Author": {"LoginName": "system", "Id": 1},
        }
        expected_response = {
            "_id": "440d5fb060e33969ac4f5425a4d56c75",
            "type": "sites",
            "title": "dummy",
            "creation_time": "2023-01-30T12:48:31Z",
            "_timestamp": "2023-01-30T12:48:31Z",
            "url": "sharepoint.com",
            "server_relative_url": "/site",
            "author": "system",
            "author_id": 1,
        }

        target_response = source.format_sites(item=list_items)
        assert target_response == expected_response


@pytest.mark.asyncio
async def test_get_sites_when_no_site_available():
    """Test get sites method with valid details"""
    async with create_sps_source() as source:
        api_response = []
        source.sharepoint_client._fetch_data_with_query = AsyncIterator(api_response)
        actual_response = None
        async for item in source.sharepoint_client.get_sites(
            site_url="sites/collection1/ctest"
        ):
            actual_response = item

        assert actual_response is None


@pytest.mark.asyncio
async def test_get_list_items():
    """Test get list items method with valid details"""
    api_response = [
        {
            "AttachmentFiles": [
                {
                    "FileName": "s3 queries.txt",
                    "ServerRelativeUrl": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4.txt",
                }
            ],
            "Title": "HelloWorld",
            "Id": 1,
            "FileRef": "/site",
            "ContentTypeId": "12345",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": True,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "Length": "12",
        },
        {
            "AttachmentFiles": {},
            "Id": 1,
            "ContentTypeId": "12345",
            "Title": "HelloWorld",
            "FileRef": "/site",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": False,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
        },
    ]
    target_response = [
        {
            "AttachmentFiles": [
                {
                    "FileName": "s3 queries.txt",
                    "ServerRelativeUrl": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4.txt",
                }
            ],
            "Title": "HelloWorld",
            "Id": 1,
            "FileRef": "/site",
            "ContentTypeId": "12345",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": True,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "Length": None,
            "_id": "1",
            "url": f"{HOST_URL}/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4.txt",
            "file_name": "s3 queries.txt",
            "server_relative_url": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4.txt",
        },
        {
            "AttachmentFiles": {},
            "Id": 1,
            "ContentTypeId": "12345",
            "Title": "HelloWorld",
            "FileRef": "/site",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": False,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "url": f"{HOST_URL}/sites/collection1/ctest/Lists/ctestlist/DispForm.aspx?ID=1&Source={HOST_URL}/sites/collection1/ctest/Lists/ctestlist/AllItems.aspx&ContentTypeId=12345",
        },
    ]
    attachment_response = {"UniqueId": "1"}
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_next_url = AsyncIterator(
            [api_response]
        )
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(attachment_response)
        )
        expected_response = []
        async for item, _ in source.sharepoint_client.get_list_items(
            list_id="620070a1-ee50-4585-b6a7-0f6210b1a69d",
            site_url=f"{HOST_URL}/sites/collection1/ctest",
            server_relative_url="/sites/collection1/ctest",
            list_relative_url="/Lists/ctestlist",
            selected_field="",
        ):
            expected_response.append(item)
        assert expected_response == target_response


@pytest.mark.asyncio
async def test_get_drive_items():
    """Test get drive items method with valid details"""
    api_response = [
        {
            "File": {
                "Length": "3356",
                "Name": "Home.txt",
                "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
                "TimeCreated": "2022-05-02T07:20:33Z",
                "TimeLastModified": "2022-05-02T07:20:34Z",
                "Title": "Home.txt",
            },
            "Folder": {"__deferred": {}},
            "Modified": "2022-05-02T07:20:35Z",
            "GUID": "111111122222222-c77f-4ed3-84ef-8a4dd87c80d0",
            "Length": "3356",
        },
        {
            "File": {},
            "Folder": {
                "Length": "3356",
                "Name": "Home.txt",
                "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
                "TimeCreated": "2022-05-02T07:20:33Z",
                "TimeLastModified": "2022-05-02T07:20:34Z",
                "Title": "Home.txt",
            },
            "Modified": "2022-05-02T07:20:35Z",
            "GUID": "111111122222222-c77f-4ed3-084ef-8a4dd87c80d0",
            "Length": "3356",
        },
    ]
    target_response = [
        (
            {
                "File": {
                    "Length": "3356",
                    "Name": "Home.txt",
                    "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
                    "TimeCreated": "2022-05-02T07:20:33Z",
                    "TimeLastModified": "2022-05-02T07:20:34Z",
                    "Title": "Home.txt",
                },
                "Folder": {"__deferred": {}},
                "Modified": "2022-05-02T07:20:35Z",
                "GUID": "111111122222222-c77f-4ed3-84ef-8a4dd87c80d0",
                "Length": "3356",
                "item_type": "File",
            },
            "/sites/enterprise/ctest/SitePages/Home.aspx",
        ),
        (
            {
                "File": {},
                "Folder": {
                    "Length": "3356",
                    "Name": "Home.txt",
                    "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
                    "TimeCreated": "2022-05-02T07:20:33Z",
                    "TimeLastModified": "2022-05-02T07:20:34Z",
                    "Title": "Home.txt",
                },
                "Modified": "2022-05-02T07:20:35Z",
                "GUID": "111111122222222-c77f-4ed3-084ef-8a4dd87c80d0",
                "Length": "3356",
                "item_type": "Folder",
            },
            None,
        ),
    ]
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_next_url = AsyncIterator(
            [api_response]
        )
        expected_response = []
        async for item in source.sharepoint_client.get_drive_items(
            list_id="620070a1-ee50-4585-b6a7-0f6210b1a69d",
            site_url="/sites/enterprise/ctest",
            server_relative_url=None,
            selected_field="",
        ):
            expected_response.append(item)
        assert target_response == expected_response


@pytest.mark.asyncio
async def test_get_docs_list_items():
    """Test get docs method for list items"""

    site_content_response = {
        "Title": "ctest",
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "Url": f"{HOST_URL}/sites/enterprise/ctest",
        "ServerRelativeUrl": "/sites/enterprise/ctest",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "Created": "2023-03-19T05:02:52Z",
    }
    item_content_response = {
        "RootFolder": {
            "ServerRelativeUrl": "/sites/enterprise/ctest/_api",
        },
        "Created": "2023-03-19T05:02:52Z",
        "BaseType": 0,
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "ParentWebUrl": "/sites/enterprise/ctest/_api",
        "Title": "HelloWorld",
    }
    list_content_response = {
        "AttachmentFiles": {},
        "Id": 1,
        "ContentTypeId": "12345",
        "Title": "HelloWorld",
        "FileRef": "/site",
        "Modified": "2022-06-20T10:04:03Z",
        "Created": "2022-06-20T10:04:03Z",
        "EditorId": 1073741823,
        "AuthorId": 1073741823,
        "Attachments": False,
        "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
        "url": "/sites/enterprise/ctest/_api",
    }
    actual_response = []
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_query = Mock(
            return_value=AsyncIterator([])
        )
        source.sharepoint_client.get_site = AsyncIterator([site_content_response])
        source.sharepoint_client.get_lists = AsyncIterator([item_content_response])
        source.sharepoint_client.get_list_items = AsyncIterator(
            [(list_content_response, None)]
        )
        async for document, _ in source.get_docs():
            actual_response.append(document)
        assert len(actual_response) == 3


@pytest.mark.asyncio
async def test_get_docs_list_items_when_relativeurl_is_not_none():
    """Test get docs method for list items"""

    site_content_response = {
        "Title": "ctest",
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "Url": f"{HOST_URL}/sites/enterprise/ctest",
        "ServerRelativeUrl": "/sites/enterprise/ctest",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "Created": "2023-03-19T05:02:52Z",
    }
    item_content_response = {
        "RootFolder": {
            "ServerRelativeUrl": "/sites/enterprise/ctest/_api",
        },
        "Created": "2023-03-19T05:02:52Z",
        "BaseType": 0,
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "ParentWebUrl": "/sites/enterprise/ctest/_api",
        "Title": "HelloWorld",
    }
    list_content_response = {
        "AttachmentFiles": {},
        "Id": 1,
        "ContentTypeId": "12345",
        "Title": "HelloWorld",
        "FileRef": "/site",
        "Modified": "2022-06-20T10:04:03Z",
        "Created": "2022-06-20T10:04:03Z",
        "EditorId": 1073741823,
        "AuthorId": 1073741823,
        "Attachments": False,
        "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
        "url": "/sites/enterprise/ctest/_api",
    }
    actual_response = []
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_query = AsyncIterator([])
        source.sharepoint_client.get_site = AsyncIterator([site_content_response])
        source.sharepoint_client.get_lists = AsyncIterator([item_content_response])
        source.sharepoint_client.get_list_items = AsyncIterator(
            [(list_content_response, "/sites/enterprise/ctest/_api")]
        )

        async for document, _ in source.get_docs():
            actual_response.append(document)
        assert len(actual_response) == 3


@pytest.mark.asyncio
async def test_get_docs_drive_items():
    """Test get docs method for drive items"""

    site_content_response = {
        "Title": "ctest",
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "Url": f"{HOST_URL}/sites/enterprise/ctest",
        "ServerRelativeUrl": "/sites/enterprise/ctest",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "Created": "2023-03-19T05:02:52Z",
    }
    item_content_response = {
        "RootFolder": {
            "ServerRelativeUrl": "/sites/enterprise/ctest/_api",
        },
        "Created": "2023-03-19T05:02:52Z",
        "BaseType": 1,
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "ParentWebUrl": "/sites/enterprise/ctest/_api",
        "Title": "HelloWorld",
    }

    drive_content_response = {
        "File": {
            "Length": "3356",
            "Name": "Home.aspx",
            "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
            "TimeCreated": "2022-05-02T07:20:33Z",
            "TimeLastModified": "2022-05-02T07:20:34Z",
            "Title": "Home.aspx",
        },
        "Folder": {"__deferred": {}},
        "Modified": "2022-05-02T07:20:35Z",
        "GUID": "111111122222222-c77f-4ed3-84ef-8a4dd87c80d0",
        "Length": "3356",
        "item_type": "File",
    }
    actual_response = []
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_query = AsyncIterator([])
        source.sharepoint_client.get_site = AsyncIterator([site_content_response])
        source.sharepoint_client.get_lists = AsyncIterator([item_content_response])
        source.sharepoint_client.get_drive_items = AsyncIterator(
            [(drive_content_response, None)]
        )

        async for document, _ in source.get_docs():
            actual_response.append(document)
        assert len(actual_response) == 3


@pytest.mark.asyncio
async def test_get_docs_drive_items_for_web_pages():
    site_content_response = {
        "Title": "ctest",
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "Url": f"{HOST_URL}/sites/enterprise/ctest",
        "ServerRelativeUrl": "/sites/enterprise/ctest",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "Created": "2023-03-19T05:02:52Z",
    }
    item_content_response = {
        "RootFolder": {
            "ServerRelativeUrl": "/sites/enterprise/ctest/_api",
        },
        "Created": "2023-03-19T05:02:52Z",
        "BaseType": 1,
        "Id": "f764b597-ed44-49be-8867-f8e9ca5d0a6e",
        "LastItemModifiedDate": "2023-03-19T05:02:52Z",
        "ParentWebUrl": "/sites/enterprise/ctest/_api",
        "Title": "Site Pages",
    }

    drive_content_response = {
        "File": {
            "Length": "3356",
            "Name": "Home.aspx",
            "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
            "TimeCreated": "2022-05-02T07:20:33Z",
            "TimeLastModified": "2022-05-02T07:20:34Z",
            "Title": "Home.aspx",
        },
        "Folder": {"__deferred": {}},
        "Modified": "2022-05-02T07:20:35Z",
        "GUID": "111111122222222-c77f-4ed3-84ef-8a4dd87c80d0",
        "Length": "3356",
        "item_type": "File",
    }
    actual_response = []
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_query = AsyncIterator([])
        source.sharepoint_client.get_site = AsyncIterator([site_content_response])
        source.sharepoint_client.get_lists = AsyncIterator([item_content_response])
        source.sharepoint_client.get_drive_items = AsyncIterator(
            [(drive_content_response, None)]
        )
        async for document, _ in source.get_docs():
            actual_response.append(document)
        assert len(actual_response) == 3


@pytest.mark.asyncio
async def test_get_docs_when_no_site_available():
    """Test get docs when site is not available method"""

    async with create_sps_source() as source:
        actual_response = []
        source.sharepoint_client.get_site = AsyncIterator([])
        source.sharepoint_client._fetch_data_with_query = Mock(
            return_value=AsyncIterator([])
        )
        source.sharepoint_client._fetch_data_with_next_url = Mock(
            return_value=AsyncIterator([])
        )
        async for document, _ in source.get_docs():
            actual_response.append(document)
        assert len(actual_response) == 0


@pytest.mark.asyncio
async def test_get_content():
    """Test the get content method"""

    response_content = "This is a dummy sharepoint body response"
    expected_attachment = {
        "id": 1,
        "server_relative_url": "/url",
        "_timestamp": "2022-06-20T10:37:44Z",
        "size": 11,
        "type": "sites",
        "file_name": "dummy.pdf",
    }
    expected_content = {
        "_id": 1,
        "_attachment": "VGhpcyBpcyBhIGR1bW15IHNoYXJlcG9pbnQgYm9keSByZXNwb25zZQ==",
        "_timestamp": "2022-06-20T10:37:44Z",
    }
    async_response = MockObjectResponse()
    async_response.aiter_bytes = AsyncIterator([bytes(response_content, "utf-8")])

    async with create_sps_source() as source:
        source.sharepoint_client._api_call = AsyncIterator([async_response])
        response_content = await source.get_content(
            document=expected_attachment,
            file_relative_url="abc.com",
            site_url="/site",
            doit=True,
        )

        assert response_content == expected_content


@pytest.mark.asyncio
async def test_get_content_with_content_extraction():
    response_content = "This is a dummy sharepoint body response"
    with (
        patch(
            "connectors.content_extraction.ContentExtraction.extract_text",
            return_value=response_content,
        ),
        patch(
            "connectors.content_extraction.ContentExtraction.get_extraction_config",
            return_value={"host": "http://localhost:8090"},
        ),
    ):
        async_response = MockObjectResponse()
        async_response.aiter_bytes = AsyncIterator([bytes(response_content, "utf-8")])
        expected_attachment = {
            "id": 1,
            "server_relative_url": "/url",
            "_timestamp": "2022-06-20T10:37:44Z",
            "size": 11,
            "type": "sites",
            "file_name": "dummy.pdf",
        }
        expected_content = {
            "_id": 1,
            "body": response_content,
            "_timestamp": "2022-06-20T10:37:44Z",
        }
        async with create_sps_source(use_text_extraction_service=True) as source:
            source.sharepoint_client._api_call = AsyncIterator([async_response])
            response_content = await source.get_content(
                document=expected_attachment,
                file_relative_url="abc.com",
                site_url="/site",
                doit=True,
            )

            assert response_content == expected_content


class ContentResponse:
    content = b"This is a dummy sharepoint body response"


@pytest.mark.asyncio
async def test_get_content_when_size_is_bigger():
    """Test the get content method when document size is greater than the allowed size limit."""
    document = {
        "id": 1,
        "_timestamp": "2022-06-20T10:37:44Z",
        "size": 1048576011,
        "title": "dummy",
        "type": "sites",
        "server_relative_url": "/sites",
        "file_name": "dummy.pdf",
    }
    async with create_sps_source() as source:
        response_content = await source.get_content(
            document=document, file_relative_url="abc.com", site_url="/site", doit=True
        )

        assert response_content is None


@pytest.mark.asyncio
async def test_get_content_when_doit_is_none():
    """Test the get content method when doit is None"""
    document = {
        "id": 1,
        "_timestamp": "2022-06-20T10:37:44Z",
        "size": 11,
        "type": "sites",
        "file_name": "dummy.pdf",
    }
    async with create_sps_source() as source:
        response_content = await source.get_content(
            document=document, file_relative_url="abc.com", site_url="/site"
        )

        assert response_content is None


@pytest.mark.asyncio
async def test_fetch_data_with_query_sites():
    """Test get invoke call for sites"""
    get_response = {
        "value": [
            {
                "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                "ServerRelativeUrl": "/sites/collection1",
                "Title": "ctest",
            }
        ]
    }
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(get_response)
        )
        actual_response = []

        async for response in source.sharepoint_client._fetch_data_with_query(
            site_url="/sites/collection1/_api/web/webs", param_name="sites"
        ):
            actual_response.extend(response)

        assert [actual_response] == [
            [
                {
                    "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                    "ServerRelativeUrl": "/sites/collection1",
                    "Title": "ctest",
                }
            ]
        ]


@pytest.mark.asyncio
async def test_fetch_data_with_query_list():
    """Test get invoke call for list"""
    get_response = {
        "value": [
            {
                "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                "ServerRelativeUrl": "/sites/collection1",
                "Title": "ctest",
            }
        ]
    }
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(get_response)
        )
        actual_response = []

        async for response in source.sharepoint_client._fetch_data_with_query(
            site_url="/sites/collection1/_api/web/webs", param_name="lists"
        ):
            actual_response.extend(response)

        assert [actual_response] == [
            [
                {
                    "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                    "ServerRelativeUrl": "/sites/collection1",
                    "Title": "ctest",
                }
            ]
        ]


@pytest.mark.asyncio
async def test_fetch_data_with_next_url_items():
    """Test get invoke call for drive item"""
    get_response = {
        "value": [
            {
                "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                "ServerRelativeUrl": "/sites/collection1",
                "Title": "ctest",
            }
        ]
    }
    actual_response = []
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(get_response)
        )
        async for response in source.sharepoint_client._fetch_data_with_next_url(
            site_url="/sites/collection1/_api/web/webs",
            list_id="123abc",
            param_name="drive_items",
        ):
            actual_response.append(response)

        assert actual_response == [
            [
                {
                    "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                    "ServerRelativeUrl": "/sites/collection1",
                    "Title": "ctest",
                }
            ]
        ]


@pytest.mark.asyncio
async def test_fetch_data_with_next_url_list_items():
    """Test get invoke call when for list item"""
    get_response = {
        "value": [
            {
                "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                "ServerRelativeUrl": "/sites/collection1",
                "Title": "ctest",
            }
        ]
    }
    actual_response = []
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(get_response)
        )
        async for response in source.sharepoint_client._fetch_data_with_next_url(
            site_url="/sites/collection1/_api/web/webs",
            list_id="123abc",
            param_name="list_items",
        ):
            actual_response.append(response)

        assert actual_response == [
            [
                {
                    "Id": "111111122222222-0fd8-471c-96aa-c75f71293131",
                    "ServerRelativeUrl": "/sites/collection1",
                    "Title": "ctest",
                }
            ]
        ]


class ClientSession:
    """Mock Client Session Class"""

    async def aclose(self):
        """Close method of Mock Client Session Class"""
        pass


@pytest.mark.asyncio
async def test_close_with_client_session():
    """Test close method of SharepointServerDataSource with client session"""

    async with create_sps_source() as source:
        source.sharepoint_client.session = ClientSession()

        await source.sharepoint_client.close_session()


@pytest.mark.asyncio
async def test_close_without_client_session():
    """Test close method of SharepointServerDataSource without client session"""

    async with create_sps_source() as source:
        source.sharepoint_client.session = None

        await source.sharepoint_client.close_session()


@pytest.mark.asyncio
async def test_api_call_negative(patch_default_wait_multiplier):
    """Tests the _api_call function while getting an exception."""

    async with create_sps_source(retry_count=2) as source:
        with patch.object(
            httpx.AsyncClient, "get", side_effect=Exception(EXCEPTION_MESSAGE)
        ):
            source.sharepoint_client.session = source.sharepoint_client._get_session()
            with pytest.raises(Exception):
                await anext(source.sharepoint_client._api_call(url_name="ping"))


@pytest.mark.asyncio
async def test_api_call_successfully():
    """Tests the _api_call function."""

    async with create_sps_source() as source:
        mocked_response = [{"name": "dummy_project", "id": "test123"}]
        mock_token = {"access_token": "test2344", "expires_in": "1234555"}
        async_response = MockResponse(mocked_response, 200)
        async_response_token = MockResponse(mock_token, 200)

        with (
            patch("httpx.AsyncClient.get", return_value=async_response),
            patch("httpx.request", return_value=async_response_token),
        ):
            source.sharepoint_client._get_session()
            async for response in source.sharepoint_client._api_call(
                url_name="ping", site_url="abc", host_url="sharepoint.com"
            ):
                assert await response == [{"name": "dummy_project", "id": "test123"}]


@pytest.fixture
def patch_default_wait_multiplier():
    with mock.patch("connectors.sources.sharepoint_server.RETRY_INTERVAL", 0):
        yield


class ClientErrorException:
    real_url = ""


@pytest.mark.asyncio
async def test_api_call_when_status_429_exception(
    patch_default_wait_multiplier, caplog
):
    async with create_sps_source(retry_count=2) as source:
        mock_response = {"access_token": "test2344", "expires_in": "1234555"}
        async_response = MockResponse(mock_response, 429)
        caplog.set_level("WARN")

        with patch.object(
            httpx.AsyncClient,
            "get",
            side_effect=httpx.HTTPError("error"),
        ):
            with patch("httpx.get", return_value=async_response):
                source.sharepoint_client._get_session()
                async for _ in source.sharepoint_client._api_call(
                    url_name="attachment", url="abc.com"
                ):
                    assert "Skipping attachment for abc.com" in caplog.text


@pytest.mark.asyncio
async def test_api_call_when_server_is_down(patch_default_wait_multiplier, caplog):
    """Tests the _api_call function while server gets disconnected."""
    async with create_sps_source(retry_count=2) as source:
        mock_response = {"access_token": "test2344", "expires_in": "1234555"}
        async_response = MockResponse(mock_response, 200)
        caplog.set_level("WARN")
        with patch.object(
            httpx.AsyncClient,
            "get",
            side_effect=httpx.ConnectError("Something went wrong"),
        ):
            with patch("httpx.get", return_value=async_response):
                source.sharepoint_client._get_session()
                async for _ in source.sharepoint_client._api_call(
                    url_name="attachment", url="abc.com"
                ):
                    assert "Skipping attachment for abc.com" in caplog.text


@pytest.mark.asyncio
async def test_get_session():
    """Test that the instance of session returned is always the same for the datasource class."""
    async with create_sps_source() as source:
        first_instance = source.sharepoint_client._get_session()
        second_instance = source.sharepoint_client._get_session()
        assert first_instance is second_instance


@pytest.mark.asyncio
async def test_get_site_pages_content():
    EXPECTED_ATTACHMENT = {
        "id": 1,
        "server_relative_url": "/url",
        "_timestamp": "2022-06-20T10:37:44Z",
        "size": 11,
        "type": "sites",
        "file_name": "dummy.pdf",
    }
    RESPONSE_DATA = {"CanvasContent1": "This is a dummy sharepoint body response"}
    EXPECTED_CONTENT = {
        "_id": 1,
        "_attachment": "VGhpcyBpcyBhIGR1bW15IHNoYXJlcG9pbnQgYm9keSByZXNwb25zZQ==",
        "_timestamp": "2022-06-20T10:37:44Z",
    }
    async with create_sps_source() as source:
        response_content = await source.get_site_pages_content(
            document=EXPECTED_ATTACHMENT,
            list_response=RESPONSE_DATA,
            doit=True,
        )
        assert response_content == EXPECTED_CONTENT


async def coroutine_generator(item):
    """create a method for returning fake coroutine value for
    Args:
        item: Value for converting into coroutine
    """
    return item


@pytest.mark.asyncio
async def test_get_site_pages_content_when_doit_is_none():
    document = {"title": "Home.aspx", "type": "File", "size": 1000000}
    async with create_sps_source() as source:
        response_content = await source.get_site_pages_content(
            document=document,
            list_response={},
            doit=None,
        )

        assert response_content is None


@pytest.mark.asyncio
async def test_get_site_pages_content_for_canvascontent1_none():
    async with create_sps_source() as source:
        EXPECTED_ATTACHMENT = {"title": "Home.aspx", "type": "File", "size": "1000000"}
        response_content = await source.get_site_pages_content(
            document=EXPECTED_ATTACHMENT,
            list_response={"CanvasContent1": None},
            doit=True,
        )
        assert response_content is None


@pytest.mark.asyncio
async def test_get_list_items_with_no_extension():
    api_response = [
        {
            "AttachmentFiles": [
                {
                    "FileName": "s3 queries",
                    "ServerRelativeUrl": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4",
                }
            ],
            "Title": "HelloWorld",
            "Id": 1,
            "FileRef": "/site",
            "ContentTypeId": "12345",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": True,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "Length": "12",
        },
    ]
    target_response = [
        {
            "AttachmentFiles": [
                {
                    "FileName": "s3 queries",
                    "ServerRelativeUrl": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4",
                }
            ],
            "Title": "HelloWorld",
            "Id": 1,
            "FileRef": "/site",
            "ContentTypeId": "12345",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": True,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "Length": None,
            "_id": "1",
            "url": f"{HOST_URL}/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4",
            "file_name": "s3 queries",
            "server_relative_url": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/v4",
        },
    ]
    attachment_response = {"UniqueId": "1"}
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_next_url = AsyncIterator(
            [api_response]
        )
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(attachment_response)
        )
        expected_response = []
        async for item, _ in source.sharepoint_client.get_list_items(
            list_id="620070a1-ee50-4585-b6a7-0f6210b1a69d",
            site_url=f"{HOST_URL}/sites/collection1/ctest",
            server_relative_url="/sites/collection1/ctest",
            list_relative_url="/Lists/ctestlist",
            selected_field="",
        ):
            expected_response.append(item)
        assert expected_response == target_response


@pytest.mark.asyncio
async def test_get_list_items_with_extension_only():
    api_response = [
        {
            "AttachmentFiles": [
                {
                    "FileName": ".env",
                    "ServerRelativeUrl": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/.env",
                }
            ],
            "Title": "HelloWorld",
            "Id": 1,
            "FileRef": "/site",
            "ContentTypeId": "12345",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": True,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "Length": "12",
        },
    ]
    target_response = [
        {
            "AttachmentFiles": [
                {
                    "FileName": ".env",
                    "ServerRelativeUrl": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/.env",
                }
            ],
            "Title": "HelloWorld",
            "Id": 1,
            "FileRef": "/site",
            "ContentTypeId": "12345",
            "Modified": "2022-06-20T10:04:03Z",
            "Created": "2022-06-20T10:04:03Z",
            "EditorId": 1073741823,
            "AuthorId": 1073741823,
            "Attachments": True,
            "GUID": "111111122222222-adfa-4e4f-93c4-bfedddda8510",
            "Length": None,
            "_id": "1",
            "url": f"{HOST_URL}/sites/collection1/ctest/Lists/ctestlist/Attachments/1/.env",
            "file_name": ".env",
            "server_relative_url": "/sites/collection1/ctest/Lists/ctestlist/Attachments/1/.env",
        },
    ]
    attachment_response = {"UniqueId": "1"}
    async with create_sps_source() as source:
        source.sharepoint_client._fetch_data_with_next_url = AsyncIterator(
            [api_response]
        )
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(attachment_response)
        )
        expected_response = []
        async for item, _ in source.sharepoint_client.get_list_items(
            list_id="620070a1-ee50-4585-b6a7-0f6210b1a69d",
            site_url=f"{HOST_URL}/sites/collection1/ctest",
            server_relative_url="/sites/collection1/ctest",
            list_relative_url="/Lists/ctestlist",
            selected_field="",
        ):
            expected_response.append(item)
        assert expected_response == target_response


async def create_fake_coroutine(data):
    """create a method for returning fake coroutine value"""
    return data


@pytest.mark.asyncio
async def test_get_access_control():
    async with create_sps_source() as source:
        source._dls_enabled = Mock(return_value=True)
        source.sharepoint_client.site_collections_path = ["collection1"]
        get_response = {
            "value": [
                {
                    "Id": 1,
                    "LoginName": "user1",
                    "Title": "user1",
                    "PrincipalType": 1,
                    "Email": "",
                    "IsSiteAdmin": False,
                    "UserId": {"NameIdIssuer": "urn:office:idp:activedirectory"},
                },
                {
                    "Id": 2,
                    "LoginName": "user1",
                    "Title": "user2",
                    "PrincipalType": 1,
                    "Email": "",
                    "IsSiteAdmin": False,
                    "UserId": {"NameIdIssuer": "urn:office:idp:activedirectory"},
                },
                {
                    "Id": 3,
                    "LoginName": "user3",
                    "Title": "user3",
                    "PrincipalType": 1,
                    "Email": "",
                    "IsSiteAdmin": False,
                    "UserId": {"NameIdIssuer": "urn:office:idp:activedirectory"},
                },
                {
                    "Id": 4,
                    "LoginName": "user4",
                    "Title": "user4",
                    "PrincipalType": 8,
                    "Email": "",
                    "IsSiteAdmin": False,
                    "UserId": {"NameIdIssuer": "urn:office:idp:activedirectory"},
                },
            ]
        }
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(get_response)
        )
        expected_users = []
        async for user in source.get_access_control():
            expected_users.append(user)
        assert len(expected_users) == 2


@pytest.mark.asyncio
async def test_get_access_control_with_dls_disabled():
    async with create_sps_source() as source:
        source._dls_enabled = Mock(return_value=False)
        access_control = []

        async for doc in source.get_access_control():
            access_control.append(doc)

        assert len(access_control) == 0


@pytest.mark.asyncio
async def test_get_docs_with_dls_enabled():
    async with create_sps_source() as source:
        source.sharepoint_client.site_collections_path = ["collection1"]
        source.sharepoint_client.fix_relative_url = Mock(return_value="sites/site1")
        source.sharepoint_client.get_site = AsyncIterator(
            [
                {
                    "ServerRelativeUrl": None,
                    "Title": "demo2",
                    "Url": "/abcd",
                    "Id": 2,
                    "LastItemModifiedDate": "2022-06-20T10:04:03Z",
                    "Created": "2022-06-20T10:04:03Z",
                }
            ]
        )
        source.sharepoint_client.get_sites = AsyncIterator(
            [
                {
                    "ServerRelativeUrl": None,
                    "Title": "demo",
                    "Url": "/abc",
                    "Id": 1,
                    "LastItemModifiedDate": "2022-06-20T10:04:03Z",
                    "Created": "2022-06-20T10:04:03Z",
                }
            ]
        )
        source._dls_enabled = Mock(return_value=True)

        source.sharepoint_client.site_role_assignments = AsyncIterator(
            [
                {
                    "Member": {
                        "odata.type": "SP.Group",
                        "Id": 1,
                        "LoginName": "i:0#.w|\\administrator",
                        "Title": "administrator",
                        "PrincipalType": 1,
                        "Email": "",
                        "IsSiteAdmin": True,
                        "UserId": {
                            "NameId": "1",
                        },
                    },
                    "RoleDefinitionBindings": [
                        {
                            "BasePermissions": {
                                "High": "2147483647",
                                "Low": "4294967295",
                            },
                            "Id": 123,
                            "Name": "Full Control",
                            "RoleTypeKind": 5,
                        },
                    ],
                    "PrincipalId": 1,
                },
                {
                    "Member": {
                        "odata.type": "SP.Group",
                        "Id": 2,
                        "LoginName": "i:0#.w|\\user1",
                        "Title": "user1",
                        "PrincipalType": 1,
                        "Email": "",
                        "IsSiteAdmin": True,
                        "UserId": {
                            "NameId": "1",
                        },
                    },
                    "PrincipalId": 1,
                },
            ]
        )
        source.sharepoint_client.site_admins = AsyncIterator(
            [
                {
                    "odata.type": "SP.User",
                    "Id": 123,
                    "LoginName": "administrator",
                    "Title": "user1",
                    "PrincipalType": 1,
                    "Email": "",
                    "IsSiteAdmin": True,
                }
            ]
        )
        source.sharepoint_client.get_lists = AsyncIterator(
            [
                {
                    "RootFolder": {
                        "ServerRelativeUrl": "/abc",
                    },
                    "BaseType": 1,
                    "Created": "2024-04-15T09:29:21Z",
                    "Description": "",
                    "Id": "4eba2cd0-46d2-4b5e-8918-b8de89c90ecc",
                    "LastItemModifiedDate": "2024-04-15T09:29:21Z",
                    "LastItemUserModifiedDate": "2024-04-15T09:29:21Z",
                    "ParentWebUrl": "/abc",
                    "Title": "list",
                }
            ]
        )
        source.sharepoint_client.site_list_has_unique_role_assignments = Mock(
            return_value=create_fake_coroutine(True)
        )
        source.sharepoint_client.site_list_item_has_unique_role_assignments = Mock(
            return_value=create_fake_coroutine(True)
        )
        source.sharepoint_client.site_role_assignments_using_title = AsyncIterator(
            [
                {
                    "Member": {
                        "odata.type": "SP.User",
                        "Id": 1,
                        "LoginName": "i:0#.w|administrator",
                        "Title": "administrator",
                        "PrincipalType": 1,
                        "Email": "",
                        "IsSiteAdmin": True,
                        "UserId": {"NameIdIssuer": "directory"},
                    },
                    "RoleDefinitionBindings": [
                        {
                            "odata.type": "SP.RoleDefinition",
                            "BasePermissions": {
                                "High": "2147483647",
                                "Low": "4294967295",
                            },
                            "Description": "Has full control.",
                            "Id": 123,
                            "Name": "Full Control",
                            "RoleTypeKind": 5,
                        },
                    ],
                    "PrincipalId": 1,
                }
            ]
        )
        source.sharepoint_client.site_list_item_role_assignments = AsyncIterator(
            [
                {
                    "Member": {
                        "odata.type": "SP.Group",
                        "Id": 1,
                        "LoginName": "i:0#.w|administrator",
                        "Title": "administrator",
                        "PrincipalType": 1,
                        "Users": [
                            {
                                "odata.type": "SP.User",
                                "Id": 13,
                                "LoginName": "group1",
                                "Title": "group1",
                                "PrincipalType": 8,
                                "Email": "",
                                "IsSiteAdmin": False,
                                "UserId": {
                                    "NameIdIssuer": "urn:office:idp:activedirectory"
                                },
                            }
                        ],
                        "Email": "",
                        "IsSiteAdmin": True,
                        "UserId": {"NameIdIssuer": "directory"},
                    },
                    "RoleDefinitionBindings": [
                        {
                            "odata.type": "SP.RoleDefinition",
                            "BasePermissions": {
                                "High": "2147483647",
                                "Low": "4294967295",
                            },
                            "Description": "Has full control.",
                            "Id": 123,
                            "Name": "Full Control",
                            "RoleTypeKind": 5,
                        },
                    ],
                    "PrincipalId": 1,
                }
            ]
        )
        source.sharepoint_client.get_drive_items = AsyncIterator(
            [
                (
                    {
                        "File": {},
                        "Folder": {
                            "Length": "3356",
                            "Name": "Home.txt",
                            "ServerRelativeUrl": "/sites/enterprise/ctest/SitePages/Home.aspx",
                            "TimeCreated": "2022-05-02T07:20:33Z",
                            "TimeLastModified": "2022-05-02T07:20:34Z",
                            "Title": "Home.txt",
                        },
                        "Modified": "2022-05-02T07:20:35Z",
                        "GUID": "111111122222222-c77f-4ed3-084ef-8a4dd87c80d0",
                        "Length": "3356",
                        "Id": 2,
                        "item_type": "Folder",
                    },
                    None,
                )
            ]
        )
        async for site_document, _ in source.get_docs():
            assert site_document is not None


@pytest.mark.asyncio
async def test_site_list_item_role_assignments():
    api_response = {"value": [{"verifying_calls_only": True}]}
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(api_response)
        )
        async for _ in source.sharepoint_client.site_list_item_role_assignments(
            site_url="/abc", site_list_name="list1", list_item_id=1
        ):
            source.sharepoint_client._api_call.assert_called_once()


@pytest.mark.asyncio
async def test_site_role_assignments_using_title():
    api_response = {"value": [{"verifying_calls_only": True}]}
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(api_response)
        )
        async for _ in source.sharepoint_client.site_role_assignments_using_title(
            site_url="/abc", site_list_name="list1"
        ):
            source.sharepoint_client._api_call.assert_called_once()


@pytest.mark.asyncio
async def test_site_admins():
    api_response = {"value": [{"verifying_calls_only": True}]}
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(api_response)
        )
        async for _ in source.sharepoint_client.site_admins(site_url="/abc"):
            source.sharepoint_client._api_call.assert_called_once()


@pytest.mark.asyncio
async def test_site_role_assignments():
    api_response = {"value": [{"verifying_calls_only": True}]}
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(api_response)
        )
        async for _ in source.sharepoint_client.site_role_assignments(site_url="/abc"):
            source.sharepoint_client._api_call.assert_called_once()


@pytest.mark.asyncio
async def test_site_list_has_unique_role_assignments():
    api_response = {"value": True}
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(api_response)
        )
        await source.sharepoint_client.site_list_has_unique_role_assignments(
            site_list_name="list1", site_url="/abc"
        )
        source.sharepoint_client._api_call.assert_called_once()


@pytest.mark.asyncio
async def test_site_list_item_has_unique_role_assignments():
    api_response = {"value": True}
    async with create_sps_source() as source:
        source.sharepoint_client._api_call = Mock(
            return_value=async_native_coroutine_generator(api_response)
        )
        await source.sharepoint_client.site_list_item_has_unique_role_assignments(
            site_url="/abc", site_list_name="list1", list_item_id=1
        )
        source.sharepoint_client._api_call.assert_called_once()
