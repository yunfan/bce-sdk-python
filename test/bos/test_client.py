#!/usr/bin/env python
#coding=utf8

# Copyright 2014 Baidu, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
# an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
"""
Test models of BOS.
"""

import os
import sys
import random
import unittest
import httplib
import StringIO
import json
import socket

import coverage
import baidubce
import bos_test_config
from baidubce.auth import bce_v1_signer
from baidubce.auth import bce_credentials
from baidubce import utils
from baidubce.services.bos import bos_client
from baidubce.services.bos import storage_class
from baidubce.exception import BceHttpClientError
from baidubce.exception import BceServerError
from baidubce.exception import BceClientError
from baidubce.http import http_methods
from baidubce.http import handler
from baidubce.bce_response import BceResponse
from baidubce import protocol
from baidubce.utils import Expando
from baidubce.utils import required
from baidubce.http import bce_http_client
from baidubce.bce_client_configuration import BceClientConfiguration
from baidubce.retry_policy import NoRetryPolicy
from baidubce.retry_policy import BackOffRetryPolicy

reload(sys)
sys.setdefaultencoding('utf8')

cov = coverage.coverage()
cov.start()

http_request = None


class MockHttpResponse(object):
    """
    mock http response
    """
    def __init__(self, status, content=None, header_list=None):
        """
        constructor
        :param status:
        :param content:
        :param header_list:
        :return:
        """
        self.status = status
        self.content = content
        self.header_list = header_list
        self.reason = "Mock"

    def read(self):
        """
        mock read
        :return:
        """
        return self.content

    def getheaders(self):
        """
        mock getheaders
        :return:
        """
        return self.header_list

    def close(self):
        """
        mock close
        :return:
        """
        pass


class MockHttpConnection(object):
    """
    mock http connection
    """
    def __init__(self):
        """
        constructor
        :return:
        """
        self.header = {}
        self.content = ""
        self.send_called = 0
        self.putrequest_called = 0
        self.putheader_called = 0
        self.endheaders_called = 0
        self.getresponse_called = 0
        self.close_called = 0

    def putrequest(self, method, uri, skip_host, skip_accept_encoding):
        """
        mock putrequest
        :param method:
        :param uri:
        :param skip_host:
        :param skip_accept_encoding:
        :return:
        """
        self.putrequest_called += 1

    def putheader(self, k, v):
        """
        mock putheader
        :param k:
        :param v:
        :return:
        """
        self.putheader_called += 1
        self.header[k] = v

    def endheaders(self):
        """
        mock endheaders
        :return:
        """
        self.endheaders_called += 1

    def getresponse(self):
        """
        mock getresponse
        :return:
        """
        self.getresponse_called += 1
        return None

    def send(self, buf):
        """
        mock send
        :param buf:
        :return:
        """
        self.content += buf
        self.send_called += 1

    def close(self):
        """
        mock close
        :return:
        """
        self.close_called += 1


class MockInputStream(object):
    """
    mock input stream
    """
    def __init__(self, content):
        """
        constructor
        :param content:
        :return:
        """
        self.content = content
        self.counter = 0

    def read(self, size):
        """
        mock read
        :param size:
        :return:
        """
        if self.counter >= len(self.content):
            return ""
        else:
            ret = self.content[self.counter:self.counter + size]
            self.counter += size
            return ret

    def close(self):
        """
        mock close
        :return:
        """
        pass


def mock_get_connection(protocol, host, port, timeout):
    """
    mock get_connection
    :param protocol:
    :param endpoint:
    :param timeout:
    :return:
    """
    return MockHttpConnection()


def mock_send_http_request_wrapper(throw, header_list):
    """
    factory function of mock send_http_request
    :param throw:
    :param header_list:
    :return:
    """
    def mock_send_http_request(conn, method, uri, headers, body, send_buf_size):
        """
        mock send_http_request
        :param conn:
        :param method:
        :param uri:
        :param headers:
        :param body:
        :param send_buf_size:
        :return:
        """
        if throw:
            raise socket.error
        else:
            return MockHttpResponse(200, header_list=header_list)
    return mock_send_http_request


def mock_handler_function_wrapper(ret):
    """
    factory function of mock handler
    :param ret:
    :return:
    """
    def mock_handler_function(http_response, response):
        """
        mock handler
        :param http_response:
        :param response:
        :return:
        """
        return ret
    return mock_handler_function


def mock_sign_function(credentials, method, path, headers, params):
    """
    mock sign function
    :param credentials:
    :param method:
    :param path:
    :param headers:
    :param params:
    :return:
    """
    return "Mocked"


def mock_client_send_request_wrapper(throw, status_code):
    """
    factory function of mock send_request
    :param throw:
    :param status_code:
    :return:
    """
    def mock_send_request(http_method, bucket_name=None, key=None,
                          body=None, headers=None, params=None,
                          config=None, body_parser=None):
        """
        mock send_request
        :param http_method:
        :param bucket_name:
        :param key:
        :param body:
        :param headers:
        :param params:
        :param config:
        :param body_parser:
        :return:
        """
        if throw:
            e = BceServerError("error", status_code=status_code)
            raise BceHttpClientError("error", e)
        else:
            pass
    return mock_send_request


class TestClient(unittest.TestCase):
    """TestClient"""
    BUCKET = "test-bucket%d" % os.getpid()
    KEY = "test_object%d" % os.getpid()
    FILENAME = "temp_file%d" % os.getpid()

    def setUp(self):
        """Start test"""
        self.bos = bos_client.BosClient(bos_test_config.config)
        if not self.bos.does_bucket_exist(self.BUCKET):
            self.bos.create_bucket(self.BUCKET)

    def tearDown(self):
        """Finish test"""
        response = self.bos.list_objects(self.BUCKET)
        for obj in response.contents:
            self.bos.delete_object(self.BUCKET, obj.key)
        self.bos.delete_bucket(self.BUCKET)

        if os.path.isfile(self.FILENAME):
            os.remove(self.FILENAME)

    def check_headers(self, response, headers_list=None):
        """check headers"""
        ordinary_headers_list = ['content_length',
                                'bce_debug_id',
                                'date',
                                'bce_request_id',
                                'server']

        for item in ordinary_headers_list:
            self.assertTrue(hasattr(response.metadata, item))

        if isinstance(headers_list, list):
            for item in headers_list:
                self.assertTrue(hasattr(response.metadata, item))

    def get_file(self, size):
        """create a file with size is size(MB)"""
        file = open(self.FILENAME, "w")
        file.seek(1024 * 1024 * size)
        file.write('\x00')
        file.close()


class TestDoesBucketExist(TestClient):
    """test does bucket exist"""
    def test_does_bucket_exist(self):
        """test does bucket exist function"""
        old_func = self.bos._send_request
        # test success
        self.bos._send_request = mock_client_send_request_wrapper(False, None)
        self.assertTrue(self.bos.does_bucket_exist("asdf"))
        # test forbidden
        self.bos._send_request = mock_client_send_request_wrapper(True, httplib.FORBIDDEN)
        self.assertTrue(self.bos.does_bucket_exist("asdf"))
        # test not found
        self.bos._send_request = mock_client_send_request_wrapper(True, httplib.NOT_FOUND)
        self.assertFalse(self.bos.does_bucket_exist("asdf"))
        # test others
        self.bos._send_request = mock_client_send_request_wrapper(True, httplib.NO_CONTENT)
        self.assertRaises(BceHttpClientError, self.bos.does_bucket_exist, "asdf")
        self.bos._send_request = old_func


class TestCopyObject(TestClient):
    """test copy_object function"""
    def test_copy_object(self):
        """test copy_object function normally"""
        response = self.bos.put_object_from_string(self.BUCKET, self.KEY, "This is a string.")
        response = self.bos.copy_object(self.BUCKET,
                                        self.KEY,
                                        self.BUCKET,
                                        "test_target_key")
        self.check_headers(response, response.etag)
        self.assertTrue(hasattr(response, 'last_modified'))
        self.assertEqual(response.etag, '13562b471182311b6eea8d241103e8f0')
        # source bucket is None
        err = None
        try:
            self.bos.copy_object(None, self.KEY, self.BUCKET, self.KEY)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
        # source key is None
        err = None
        try:
            self.bos.copy_object(self.BUCKET, None, self.BUCKET, self.KEY)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
        # target bucket is None
        err = None
        try:
            self.bos.copy_object(self.BUCKET, self.KEY, None, self.KEY)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
        # target key is None
        err = None
        try:
            self.bos.copy_object(self.BUCKET, self.KEY, self.BUCKET, None)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
        err = None
        try:
            self.bos.copy_object(None, self.KEY, self.BUCKET, self.KEY)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)

    def test_copy_object_ia(self):
        """test copy_object standard ia"""
        # test copy object standard ia
        response = self.bos.put_object_from_string(self.BUCKET, self.KEY, "This is a string.")
        response = self.bos.copy_object(source_bucket_name=self.BUCKET,
                                        source_key=self.KEY,
                                        target_bucket_name=self.BUCKET,
                                        target_key="test_target_key_ia",
                                        storage_class=storage_class.STANDARD_IA)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, 
                                                 key='test_target_key_ia')
        # copy not apply standard ia
        self.assertIsNone(response.metadata.bce_storage_class)
        # test copy object default
        response = self.bos.copy_object(source_bucket_name=self.BUCKET,
                                        source_key=self.KEY,
                                        target_bucket_name=self.BUCKET,
                                        target_key="test_target_key_default")

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, 
                                                 key='test_target_key_default')
        self.assertIsNone(response.metadata.bce_storage_class)
        # test copy object standard
        response = self.bos.copy_object(source_bucket_name=self.BUCKET,
                                        source_key=self.KEY,
                                        target_bucket_name=self.BUCKET,
                                        target_key="test_target_key_standard",
                                        storage_class=storage_class.STANDARD)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, 
                                                 key='test_target_key_standard')
        self.assertIsNone(response.metadata.bce_storage_class)
        # test copy object invalid storage class
        err = None
        try:
            self.bos.copy_object(source_bucket_name=self.BUCKET,
                                 source_key=self.KEY,
                                 target_bucket_name=self.BUCKET,
                                 target_key="test_target_key_invalid",
                                 storage_class="aaa")
        except Exception as e:
            err = e
        except BceServerError as bse:
            err = bse
        finally:
            self.assertIsNotNone(err)
        # test copy object case
        err = None
        try:
            self.bos.copy_object(source_bucket_name=self.BUCKET,
                                 source_key=self.KEY,
                                 target_bucket_name=self.BUCKET,
                                 target_key="test_target_key_case",
                                 storage_class=" STANDARD_Ia ")
        except ValueError as e:
            err = e
        finally:
            self.assertIsNone(err)


class TestGeneratePreSignedUrl(TestClient):
    """test generate_pre_signed_url function"""
    def test_generate_pre_signed_url(self):
        """test generate_pre_signed_url normally"""
        url = self.bos.generate_pre_signed_url(self.BUCKET, self.KEY, timestamp=1,
                                               expiration_in_seconds=100000000)
        self.assertEqual(url, self.bos.generate_pre_signed_url(self.BUCKET, self.KEY, timestamp=1,
                                                              expiration_in_seconds=100000000))


class TestListMultipartsUploads(TestClient):
    """test get_all_multi_upload function"""
    def test_list_multipart_uploads(self):
        """test _list_multipart_uploads function normally"""
        time1 = utils.get_canonical_time()
        upload_id1 = self.bos.initiate_multipart_upload\
            (self.BUCKET, "aaa").upload_id

        time2 = utils.get_canonical_time()
        upload_id2 = self.bos.initiate_multipart_upload\
            (self.BUCKET, "bbb").upload_id

        response = self.bos.list_multipart_uploads(self.BUCKET, max_uploads=1)
        self.check_headers(response)
        self.assertEqual(response.bucket, self.BUCKET)
        self.assertEqual(response.max_uploads, 1)
        self.assertEqual(response.prefix, '')
        self.assertTrue(response.is_truncated)
        self.assertEqual(response.key_marker, '')

        for item in response.uploads:
            self.assertEqual(item.key, 'aaa')
            self.assertEqual(item.upload_id, upload_id1)
            self.assertEqual(item.owner.id, bos_test_config.OWNER_ID)
            self.assertEqual(item.owner.display_name, bos_test_config.DISPLAY_NAME)
            self.assertEqual(item.initiated, time1)

        response = self.bos.list_multipart_uploads(self.BUCKET, max_uploads=1, key_marker='aaa')
        for item in response.uploads:
            self.assertEqual(item.key, 'bbb')
            self.assertEqual(item.upload_id, upload_id2)
            self.assertEqual(item.owner.id, bos_test_config.OWNER_ID)
            self.assertEqual(item.owner.display_name, bos_test_config.DISPLAY_NAME)
            self.assertEqual(item.initiated, time2)

    def test_list_all_multipart_uploads(self):
        """test list_all_multipart_uploads function normally"""
        time1 = utils.get_canonical_time()
        upload_id1 = self.bos.initiate_multipart_upload\
            (self.BUCKET, "aaa").upload_id

        time2 = utils.get_canonical_time()
        upload_id2 = self.bos.initiate_multipart_upload\
            (self.BUCKET, "bbb").upload_id

        key_list = [u'aaa', u'bbb']
        id_list = [upload_id1, upload_id2]
        time_list = [time1, time2]
        for item, key, id, timestamp in zip(self.bos.list_all_multipart_uploads(self.BUCKET),
                                            key_list,
                                            id_list,
                                            time_list):
            self.assertEqual(item.key, key)
            self.assertEqual(item.upload_id, id)
            self.assertEqual(item.owner.id, bos_test_config.OWNER_ID)
            self.assertEqual(item.owner.display_name, bos_test_config.DISPLAY_NAME)
            self.assertEqual(item.initiated, timestamp)


class TestSetBucketAcl(TestClient):
    """test set_bucket_acl"""
    def test_set_bucket_acl(self):
        """test set_bucket_acl, which set bucket acl from Grant list"""
        grant_list = list()
        grant_list.append({'grantee':
                            [{'id': 'a0a2fe988a774be08978736ae2a1668b'},
                            {'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}],
                           'permission': ['FULL_CONTROL']})
        err = None
        try:
            self.bos.set_bucket_acl(self.BUCKET, grant_list)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)

    def test_set_bucket_canned_acl(self):
        """test set_bucket_canned_acl, set bucket acl from header and set canned acl"""
        err = None
        try:
            self.bos.set_bucket_canned_acl(self.BUCKET, "public-read-write")
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)


class TestGetBucketAcl(TestClient):
    """test get_bucket_acl function"""
    def test_get_bucket_acl(self):
        """test get_bucket_acl function normally"""
        grant_list = list()
        grant_list.append({'grantee':
                            [{'id': 'a0a2fe988a774be08978736ae2a1668b'},
                            {'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}],
                           'permission': ['FULL_CONTROL']})
        err = None
        try:
            self.bos.set_bucket_acl(self.BUCKET, grant_list)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)

        err = None
        try:
            response = self.bos.get_bucket_acl(self.BUCKET)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)
        self.assertEqual(response.owner.id, bos_test_config.OWNER_ID)
        self.assertEqual(response.access_control_list[0].grantee[0].id,
                         'a0a2fe988a774be08978736ae2a1668b')
        self.assertEqual(response.access_control_list[0].grantee[1].id,
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
        self.assertListEqual(response.access_control_list[0].permission,
                             ["FULL_CONTROL"])

        err = None
        try:
            self.bos.set_bucket_acl(self.BUCKET, response.access_control_list)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)


class TestGetBucketLocation(TestClient):
    """test get_bucket_location"""
    def test_get_bucket_location(self):
        """test get_bucket_location normally"""
        response = None
        err = None
        try:
            self.bos.get_bucket_location(self.BUCKET)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.assertEqual(response, "bj")


class TestGetObjectMetaData(TestClient):
    """test get_object_meta_data function"""
    def test_get_object_meta_data(self):
        """test get_object_meta_data function normally"""
        options = dict()
        options["private"] = "This is private."
        err = None
        try:
            self.bos.put_object_from_string(self.BUCKET,
                                            self.KEY,
                                            "This is a string.",
                                            user_metadata=options)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)

        err = None
        try:
            response = self.bos.get_object_meta_data(self.BUCKET, self.KEY)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)
        self.assertEqual(response.metadata.etag, '13562b471182311b6eea8d241103e8f0')
        self.assertEqual(response.metadata.content_length, str(len("This is a string.")))
        self.assertEqual(response.metadata.bce_meta_private, "This is private.")


class TestGetObject(TestClient):
    """test get_object function"""
    def test_get_object(self):
        """test get_object function normally"""
        err = None
        try:
            response = self.bos.put_object_from_string(self.BUCKET,
                                                       self.KEY,
                                                       "This is a string.",
                                                       user_metadata={"private": "private"})
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)

        err = None
        try:
            response = self.bos.get_object(self.BUCKET, self.KEY)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)

        self.check_headers(response)
        self.assertEqual(response.metadata.etag, '13562b471182311b6eea8d241103e8f0')
        self.assertDictEqual(response.metadata.user_metadata, {u"private":u"private"})

    def test_get_object_as_string(self):
        """test get_object_as_string function normally"""
        err = None
        try:
            response = self.bos.put_object_from_string(self.BUCKET,
                                                       self.KEY,
                                                       "This is a string.")
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)

        err = None
        try:
            response = self.bos.get_object_as_string(self.BUCKET, self.KEY)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)

        self.assertEqual(response, "This is a string.")

    def test_get_object_to_file(self):
        """test get_object_to_file function normally"""
        err = None
        try:
            response = self.bos.put_object_from_string(self.BUCKET, self.KEY, "This is a string.")
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)

        err = None
        try:
            response = self.bos.get_object_to_file(self.BUCKET, self.KEY, "Filename")
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
            os.remove("Filename")

        self.check_headers(response)
        self.assertEqual(response.metadata.etag, '13562b471182311b6eea8d241103e8f0')

    def test_get_object_to_file_with_range(self):
        """test get_object_to_file function with range"""
        err = None
        try:
            response = self.bos.put_object_from_string(self.BUCKET, self.KEY, "a" * 1000)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)

        fp = open(self.FILENAME, "w")
        fp.write('a' * 100)
        fp.close()

        fp = open(self.FILENAME, "r")
        md5 = utils.get_md5_from_fp(fp)
        fp.close()
        os.remove(self.FILENAME)

        err = None
        try:
            response = self.bos.get_object_to_file(self.BUCKET,
                                                   self.KEY,
                                                   self.FILENAME,
                                                   range=(0, 99))
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNone(err)
        self.check_headers(response)
        self.assertEqual(response.metadata.content_range, 'bytes 0-99/1000')

        fp = open(self.FILENAME, "r")
        get_md5 = utils.get_md5_from_fp(fp)
        fp.close()
        self.assertEqual(get_md5, md5)


class TestListBuckets(TestClient):
    """test list_bucket function"""
    def test_list_bucket(self):
        """test list_bucket function normally"""

        if self.bos.does_bucket_exist("aaaaaaxzr1"):
            self.bos.delete_bucket("aaaaaaxzr1")
        if self.bos.does_bucket_exist("aaaaaaxzr2"):
            self.bos.delete_bucket("aaaaaaxzr2")

        time1 = utils.get_canonical_time()
        self.bos.create_bucket("aaaaaaxzr1")

        time2 = utils.get_canonical_time()
        self.bos.create_bucket("aaaaaaxzr2")

        response = self.bos.list_buckets()
        self.check_headers(response)

        self.assertEqual(response.owner.id, bos_test_config.OWNER_ID)
        self.assertEqual(response.owner.display_name, bos_test_config.DISPLAY_NAME)
        for bucket in response.buckets:
            if bucket.name == "aaaaaaxzr1":
                self.assertEqual(bucket.creation_date[0:-4], str(time1)[0:-4])
            elif bucket.name == "aaaaaaxzr2":
                self.assertEqual(bucket.creation_date[0:-4], str(time2)[0:-4])
        self.bos.delete_bucket("aaaaaaxzr1")
        self.bos.delete_bucket("aaaaaaxzr2")


class TestListObjects(TestClient):
    """test list_objects function"""
    def test_list_objects(self):
        """test list_objects function normally"""
        for i in range(0, 10):
            self.bos.put_object_from_string(self.BUCKET, "test_object_%s" % str(random.random()),
                                            "This is a string.")

        response = self.bos.list_objects(self.BUCKET, prefix="", delimiter="")
        self.check_headers(response)
        self.assertFalse(response.is_truncated)
        self.assertEqual(response.max_keys, 1000)
        self.assertEqual(response.name, self.BUCKET)
        self.assertEqual(response.prefix, "")

    def test_list_object_with_max_keys(self):
        """test list_objects function with max_keys"""
        for i in range(0, 9):
            self.bos.put_object_from_string(self.BUCKET, "test_object_%s" % str(random.random()),
                                            "This is a string.")

            response = self.bos.list_objects(self.BUCKET)

            all_list = list()
            tmp_list = list()

            for item in response.contents:
                all_list.append(item.key)

            response = self.bos.list_objects(self.BUCKET, max_keys=4)
            for item in response.contents:
                tmp_list.append(item.key)

            response = self.bos.list_objects(self.BUCKET, max_keys=5, marker=tmp_list[-1])
            for item in response.contents:
                tmp_list.append(item.key)

            self.assertListEqual(all_list, tmp_list)

    def test_list_all_object(self):
        """test list_all_objects function"""
        object_keys = []
        for i in range(0, 9):
            object_key = "test_object_%s" % str(random.random())
            self.bos.put_object_from_string(self.BUCKET, object_key,
                                            "This is a string.")
            object_keys.append(object_key)
        for item in self.bos.list_all_objects(self.BUCKET):
            self.assertTrue(item.key in object_keys)


class TestListParts(TestClient):
    """test list_parts function"""
    def test_list_parts(self):
        """test list_parts function normally"""
        self.get_file(5)
        time1 = utils.get_canonical_time()
        upload_id = self.bos.initiate_multipart_upload(self.BUCKET,
                                                       self.KEY).upload_id
        time2 = utils.get_canonical_time()
        response = self.bos.upload_part_from_file(self.BUCKET,
                                                  self.KEY,
                                                  upload_id,
                                                  part_number=1,
                                                  part_size=100,
                                                  file_name=self.FILENAME,
                                                  offset=0)
        self.assertEqual(response.metadata.content_length, '0')
        self.assertEqual(response.metadata.etag, "6d0bb00954ceb7fbee436bb55a8397a9")

        response = self.bos.list_parts(self.BUCKET, self.KEY, upload_id)
        self.check_headers(response)
        self.assertEqual(response.bucket, self.BUCKET)
        self.assertEqual(response.initiated, time1)
        self.assertFalse(response.is_truncated)

        self.assertEqual(response.owner.id, bos_test_config.OWNER_ID)
        self.assertEqual(response.owner.display_name, bos_test_config.DISPLAY_NAME)
        self.assertEqual(response.upload_id, upload_id)
        self.assertEqual(response.next_part_number_marker, 1)
        self.assertEqual(response.part_number_marker, 0)

        for item in response.parts:
            self.assertEqual(item.etag, "6d0bb00954ceb7fbee436bb55a8397a9")
            self.assertEqual(item.size, 100)
            self.assertEqual(item.part_number, 1)
            self.assertEqual(item.last_modified, time2)

    def test_list_all_parts(self):
        """test list_all_parts function normally"""
        self.get_file(5)
        time1 = utils.get_canonical_time()
        upload_id = self.bos.initiate_multipart_upload(self.BUCKET,
                                                       self.KEY).upload_id
        time2 = utils.get_canonical_time()
        response = self.bos.upload_part_from_file(self.BUCKET,
                                                  self.KEY,
                                                  upload_id,
                                                  part_number=1,
                                                  part_size=100,
                                                  file_name=self.FILENAME,
                                                  offset=0)
        self.assertEqual(response.metadata.content_length, '0')
        self.assertEqual(response.metadata.etag, "6d0bb00954ceb7fbee436bb55a8397a9")
        for item in self.bos.list_all_parts(self.BUCKET, self.KEY, upload_id):
            self.assertEqual(item.etag, "6d0bb00954ceb7fbee436bb55a8397a9")
            self.assertEqual(item.size, 100)
            self.assertEqual(item.part_number, 1)
            self.assertEqual(item.last_modified, time2)


class TestPutObject(TestClient):
    """test put_object"""
    def test_put_object_from_string(self):
        """test put_object_from_string function normally"""
        response = self.bos.put_object_from_string(self.BUCKET, self.KEY, "Hello World")
        self.check_headers(response)
        response = self.bos.put_object_from_string(self.BUCKET, self.KEY, u"Hello World")
        self.check_headers(response)
        response = self.bos.put_object_from_string(self.BUCKET,
                                                   self.KEY,
                                                   u"Hello World")
        self.check_headers(response)
        err = None
        try:
            response = self.bos.put_object_from_string(self.BUCKET, self.KEY, 123)
        except TypeError as e:
            err = e
        finally:
            self.assertIsNotNone(err)

    def test_put_object_from_string_ia(self):
        """test put_object_from_string standard_ia"""
        # test standard ia
        response = self.bos.put_object_from_string(bucket=self.BUCKET, 
                                                   key="testia", 
                                                   data='Hello World', 
                                                   storage_class=storage_class.STANDARD_IA)
        self.check_headers(response)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key='testia')
        self.assertEqual(response.metadata.bce_storage_class, "STANDARD_IA")
        # test default storage class
        response = self.bos.put_object_from_string(bucket=self.BUCKET, 
                                                   key="testdefault", 
                                                   data='Hello World')
        self.check_headers(response)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key='testdefault')
        self.assertIsNone(response.metadata.bce_storage_class)
        # test standard
        response = self.bos.put_object_from_string(bucket=self.BUCKET, 
                                                   key="teststandard", 
                                                   data='Hello World', 
                                                   storage_class=storage_class.STANDARD)
        self.check_headers(response)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key='teststandard')
        self.assertIsNone(response.metadata.bce_storage_class)
        # test invalid storage class
        err = None
        try:
            self.bos.put_object_from_string(bucket=self.BUCKET, 
                                            key="testinvalid", 
                                            data='Hello World', 
                                            storage_class='aaa')
        except Exception as e:
            err = e
        except BceServerError as bse:
            err = bse
        finally:
            self.assertIsNotNone(err)
        # test storage case
        err = None
        try:
            self.bos.put_object_from_string(bucket=self.BUCKET, 
                                            key="testcase", 
                                            data='Hello World', 
                                            storage_class=' STANDARD_Ia ')
        except ValueError as e:
            err = e
        finally:
            self.assertIsNone(err)

    def test_put_object_from_file(self):
        """test put_object_from_file function normally"""
        self.get_file(20)
        response = self.bos.put_object_from_file(self.BUCKET, self.KEY, self.FILENAME)
        self.check_headers(response, ["etag"])

    def test_put_object_from_file_ia(self):
        """test put_object_from_file standard ia"""
        self.get_file(5)
        # test standard ia
        response = self.bos.put_object_from_file(bucket=self.BUCKET, 
                                                 key="testia", 
                                                 file_name=self.FILENAME, 
                                                 storage_class=storage_class.STANDARD_IA)
        self.check_headers(response)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key='testia')
        self.assertEqual(response.metadata.bce_storage_class, "STANDARD_IA")
        # test default storage class
        response = self.bos.put_object_from_file(bucket=self.BUCKET, 
                                                 key="testdefault", 
                                                 file_name=self.FILENAME)
        self.check_headers(response)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key='testdefault')
        self.assertIsNone(response.metadata.bce_storage_class)
        # test standard
        response = self.bos.put_object_from_file(bucket=self.BUCKET, 
                                                 key="teststandard", 
                                                 file_name=self.FILENAME, 
                                                 storage_class=storage_class.STANDARD)
        self.check_headers(response)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key='teststandard')
        self.assertIsNone(response.metadata.bce_storage_class)
        # test invalid storage class
        err = None
        try:
            self.bos.put_object_from_file(bucket=self.BUCKET, 
                                          key="testinvalid", 
                                          file_name=self.FILENAME, 
                                          storage_class='aaa')
        except Exception as e:
            err = e
        except BceServerError as bse:
            err = bse
        finally:
            self.assertIsNotNone(err)
        # test storage case
        err = None
        try:
            self.bos.put_object_from_file(bucket=self.BUCKET, 
                                          key="testcase", 
                                          file_name=self.FILENAME, 
                                          storage_class=' STANDARD_Ia ')
        except ValueError as e:
            err = e
        finally:
            self.assertIsNone(err)

    def test_put_object_exceptions(self):
        """test of exceptions in put object"""
        # key is None
        err = None
        try:
            self.bos.put_object(self.BUCKET, None, None, 100, None)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
        # too long
        err = None
        try:
            self.bos.put_object(self.BUCKET, self.KEY, None, 6 * 1024 * 1024 * 1024, None)
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)


class TestMultiUploadFile(TestClient):
    """test multi_upload_file"""
    def test_multi_upload_file(self):
        """
        test multi_upload_file, contains initiate, upload and complete.
        """
        response = self.bos.initiate_multipart_upload(self.BUCKET, self.KEY)
        self.check_headers(response)
        self.assertTrue(hasattr(response, "upload_id"))
        self.assertTrue(hasattr(response, "bucket"))
        self.assertTrue(hasattr(response, "key"))

        upload_id = response.upload_id

        self.get_file(20)
        left_size = os.path.getsize(self.FILENAME)
        done = 0
        part_number = 1
        part_list = []
        while left_size > 0:
            part_size = 5 * 1024 * 1024
            if left_size < part_size:
                part_size = left_size

            response = self.bos.upload_part_from_file(self.BUCKET,
                                                      self.KEY,
                                                      upload_id,
                                                      part_number=part_number,
                                                      part_size=part_size,
                                                      file_name = self.FILENAME,
                                                      offset=done)
            left_size = left_size - part_size
            done = done + part_size
            part_list.append({
                "partNumber": part_number,
                "eTag": response.metadata.etag
            })
            part_number += 1

        response = self.bos.complete_multipart_upload(self.BUCKET, self.KEY, upload_id=upload_id,
                                                      part_list=part_list)
        self.check_headers(response)
        self.assertTrue(hasattr(response, "etag"))
        self.assertTrue(hasattr(response, "bucket"))
        self.assertTrue(hasattr(response, "key"))
        self.assertTrue(hasattr(response, "location"))

    def test_multi_upload_file_ia(self):
        """test multi upload standard ia"""
        response = self.bos.initiate_multipart_upload(bucket_name=self.BUCKET, 
                                                      key=self.KEY,
                                                      storage_class=storage_class.STANDARD_IA)
        upload_id = response.upload_id
        self.get_file(5)
        part_list = []
        response = self.bos.upload_part_from_file(bucket_name=self.BUCKET,
                                                  key=self.KEY,
                                                  upload_id=upload_id,
                                                  part_number=1,
                                                  part_size=os.path.getsize(self.FILENAME),
                                                  file_name=self.FILENAME,
                                                  offset = 0)
        part_list.append({"partNumber": 1, "eTag": response.metadata.etag})

        response = self.bos.list_multipart_uploads(bucket_name=self.BUCKET)
        for upload in response.uploads:
            self.assertEqual(upload.storage_class, "STANDARD_IA")

        response = self.bos.list_parts(bucket_name=self.BUCKET, 
                                       key=self.KEY, 
                                       upload_id=upload_id)
        self.assertEqual(response.storage_class, "STANDARD_IA")

        self.bos.complete_multipart_upload(bucket_name=self.BUCKET,
                                           key=self.KEY,
                                           upload_id=upload_id,
                                           part_list=part_list)

        response = self.bos.get_object_meta_data(bucket_name=self.BUCKET, key=self.KEY)
        self.assertEqual(response.metadata.bce_storage_class, "STANDARD_IA")

        # test list object
        response = self.bos.list_objects(bucket_name=self.BUCKET)
        for obj in response.contents:
            if obj.key == self.KEY:
                self.assertEqual(obj.storage_class, "STANDARD_IA")


class TestAbortMultipartUpload(TestClient):
    """test abort_multipart upload"""
    def test_abort_multipart_upload(self):
        """test abort_multipart upload function normally"""
        response = self.bos.initiate_multipart_upload(self.BUCKET, self.KEY)
        self.check_headers(response)
        upload_id = response.upload_id
        self.bos.abort_multipart_upload(self.BUCKET, self.KEY, upload_id)
        self.check_headers(response)


class TestAuthorization(unittest.TestCase):
    """TestAuthorization"""
    def test_get_canonical_headers(self):
        """test_get_canonical_headers"""
        headers = {
            "Host": "localhost",
            "x-bce-a": "a/b:c",
            "C": ""
        }
        header = bce_v1_signer._get_canonical_headers(headers)
        self.assertTrue(header.split("\n")[0].startswith('host'))
        self.assertTrue(header.split("\n")[1].startswith('x-bce-a'))
        headers["Content-MD5"] = "MD5"
        self.assertEqual(bce_v1_signer._get_canonical_headers(headers),
                         "content-md5:MD5\nhost:localhost\nx-bce-a:a%2Fb%3Ac")

    def test_get_sign(self):
        """test_get_sign"""
        method = "PUT"
        uri = "/bucket/object1"
        params = {
            "A": None,
            "b": "",
            "C": "d"
        }

        headers = {
            "Host": "bce.baidu.com",
            "abc": "123",
            "x-bce-meta-key1": "ABC"
        }

        auth = bce_v1_signer.sign(bce_credentials.BceCredentials("my_ak", "my_sk"),
                                  method,
                                  uri,
                                  headers,
                                  params)
        # self.assertEqual('bce-auth-v1/bb37e6dfffc948a59eb5ddd254263809/2014-06-13T05:57:36Z/'
        #                  '1800//a20b4334ce9ba6fbddcb4064cd62a4d57ab2d78b44160b5d6ea9321beb8ca8cd',
        #                 bce_v1_signer.sign(bos_test_config.config.credentials,
        #                                    http_methods.PUT,
        #                                    uri,
        #                                    headers,
        #                                    params,
        #                                    1402639056,
        #                                    1800))


class TestUtil(unittest.TestCase):
    """TestUtil"""
    def test_get_timestamp(self):
        """test_get_timestamp"""
        self.assertEqual("1970-01-01T00:00:01Z", utils.get_canonical_time(1))
        self.assertRegexpMatches(utils.get_canonical_time(),
                                 "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

    def test_get_md5_from_fp(self):
        """test_get_md5_from_fp"""
        fp = StringIO.StringIO("abcdefghijklmnopqrstuvwxyz")
        self.assertEqual("w/zT12GS5AB9+0lsymfhOw==", utils.get_md5_from_fp(fp))
        self.assertEqual("6JmFCETYQztJTRLQhmQi2w==", utils.get_md5_from_fp(fp, 10))
        self.assertEqual("J1OsDoUaJj/azvjYRAHgwA==", utils.get_md5_from_fp(fp, 10, 10))
        self.assertEqual("6JmFCETYQztJTRLQhmQi2w==", utils.get_md5_from_fp(fp, 10, 100))

    def test_is_ip(self):
        """test_is_ip"""
        self.assertEqual(True, utils.is_ip("192.168.0.1"))
        self.assertEqual(True, utils.is_ip("localhost"))
        self.assertEqual(False, utils.is_ip("276.20.22.22"))
        self.assertEqual(False, utils.is_ip("123"))
        self.assertEqual(False, utils.is_ip("-1.33.22.11"))
        self.assertEqual(False, utils.is_ip("aaabbbccc"))
        self.assertEqual(False, utils.is_ip(123))

    def test_convert_to_standard_string(self):
        """test_convert_utf8"""
        self.assertEqual("aaa", utils.convert_to_standard_string(u"aaa"))
        self.assertEqual("北京", utils.convert_to_standard_string("北京"))

    def test_convert_header2map(self):
        """test_convert_header2map"""
        tmp_map = {"region": "beijing", "sex": "female", "age": 21}
        tmp_list = (("region", "beijing"), ("sex", "female"), ("age", 21))
        self.assertEqual(tmp_map, utils.convert_header2map(tmp_list))

    def test_check_redirect(self):
        """test_check_redirect"""
        tmp_res = httplib.HTTPSConnection("localhost")
        tmp_res.status = 301
        self.assertEqual(True, utils.check_redirect(tmp_res))
        tmp_res.status = 302
        self.assertEqual(True, utils.check_redirect(tmp_res))
        tmp_res.status = 100
        self.assertEqual(False, utils.check_redirect(tmp_res))
        tmp_res.status = "aaa"
        self.assertEqual(False, utils.check_redirect(tmp_res))
        self.assertFalse(utils.check_redirect(123))

    #def test_get_resource(self):
    def test_normalize_string(self):
        """test_normalize_string"""
        self.assertEqual("www.baidu.com", utils.normalize_string("www.baidu.com"))
        self.assertEqual("www.bai%5E%26%2A.com", utils.normalize_string("www.bai^&*.com"))
        self.assertEqual("www.baidu.com", utils.normalize_string("www.baidu.com", True))

    #def test_append_param
    def test_check_bucket_valid(self):
        """test_check_bucket_valid"""
        self.assertEqual(True, utils.check_bucket_valid("test"))
        self.assertEqual(False, utils.check_bucket_valid(""))
        self.assertEqual(False, utils.check_bucket_valid("as&*"))
        self.assertEqual(False, utils.check_bucket_valid(" asfd"))
        self.assertFalse(utils.check_bucket_valid("asdf-"))
        self.assertFalse(utils.check_bucket_valid("asdf_"))

    def test_safe_get_element(self):
        """test safe_get_element"""
        self.assertEqual(utils.safe_get_element("asdf", {}), "")
        self.assertEqual(utils.safe_get_element("asdf", {"asdf":1}), 1)

    def test_get_normalized_char_list(self):
        """test _get_normalized_char_list"""
        chars = ['%00', '%01', '%02', '%03', '%04', '%05', '%06', '%07', '%08', '%09', '%0A',
                 '%0B', '%0C', '%0D', '%0E', '%0F', '%10', '%11', '%12', '%13', '%14', '%15',
                 '%16', '%17', '%18', '%19', '%1A', '%1B', '%1C', '%1D', '%1E', '%1F', '%20',
                 '%21', '%22', '%23', '%24', '%25', '%26', '%27', '%28', '%29', '%2A', '%2B',
                 '%2C', '-', '.', '%2F', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '%3A',
                 '%3B', '%3C', '%3D', '%3E', '%3F', '%40', 'A', 'B', 'C', 'D', 'E', 'F', 'G',
                 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W',
                 'X', 'Y', 'Z', '%5B', '%5C', '%5D', '%5E', '_', '%60', 'a', 'b', 'c', 'd', 'e',
                 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u',
                 'v', 'w', 'x', 'y', 'z', '%7B', '%7C', '%7D', '~', '%7F', '%80', '%81', '%82',
                 '%83', '%84', '%85', '%86', '%87', '%88', '%89', '%8A', '%8B', '%8C', '%8D',
                 '%8E', '%8F', '%90', '%91', '%92', '%93', '%94', '%95', '%96', '%97', '%98',
                 '%99', '%9A', '%9B', '%9C', '%9D', '%9E', '%9F', '%A0', '%A1', '%A2', '%A3',
                 '%A4', '%A5', '%A6', '%A7', '%A8', '%A9', '%AA', '%AB', '%AC', '%AD', '%AE',
                 '%AF', '%B0', '%B1', '%B2', '%B3', '%B4', '%B5', '%B6', '%B7', '%B8', '%B9',
                 '%BA', '%BB', '%BC', '%BD', '%BE', '%BF', '%C0', '%C1', '%C2', '%C3', '%C4',
                 '%C5', '%C6', '%C7', '%C8', '%C9', '%CA', '%CB', '%CC', '%CD', '%CE', '%CF',
                 '%D0', '%D1', '%D2', '%D3', '%D4', '%D5', '%D6', '%D7', '%D8', '%D9', '%DA',
                 '%DB', '%DC', '%DD', '%DE', '%DF', '%E0', '%E1', '%E2', '%E3', '%E4', '%E5',
                 '%E6', '%E7', '%E8', '%E9', '%EA', '%EB', '%EC', '%ED', '%EE', '%EF', '%F0',
                 '%F1', '%F2', '%F3', '%F4', '%F5', '%F6', '%F7', '%F8', '%F9', '%FA', '%FB',
                 '%FC', '%FD', '%FE', '%FF']
        self.assertListEqual(chars, utils._get_normalized_char_list())


class TestHandler(TestClient):
    """test abort handler"""
    def test_parse_error(self):
        """test abort parse_error function in handler"""
        # test normal 2xx
        http_response = MockHttpResponse(status=208)
        self.assertFalse(handler.parse_error(http_response, None))

        # test abnormal 1xx
        http_response = MockHttpResponse(status=108)
        err = None
        try:
            handler.parse_error(http_response, None)
        except BceClientError as e:
            err = e
        finally:
            self.assertIsNotNone(err)

        # test abnormal 3xx 4xx 5xx with json body
        json_content = {"message": "error",
                        "code": 123,
                        "requestId": 12345}
        http_response = MockHttpResponse(status=508, content=json.dumps(json_content))
        err = None
        try:
            handler.parse_error(http_response, None)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
            self.assertEqual(e.message, "error")
            self.assertEqual(e.code, 123)
            self.assertEqual(e.request_id, 12345)
            self.assertEqual(e.status_code, 508)

        # test abnormal 3xx 4xx 5xx without json body
        http_response = MockHttpResponse(status=508)
        response = BceResponse()
        response.metadata.bce_request_id = 12345
        err = None
        try:
            handler.parse_error(http_response, response)
        except BceServerError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
            self.assertEqual(e.message, "Mock")
            self.assertEqual(e.request_id, 12345)
            self.assertEqual(e.status_code, 508)

    def test_parse_json(self):
        """test abort parse_json function in handler"""
        # test has body
        json_content = {"message": "error",
                        "code": 123,
                        "requestId": 12345}
        http_response = MockHttpResponse(status=508, content=json.dumps(json_content))
        response = BceResponse()
        self.assertTrue(handler.parse_json(http_response, response))
        self.assertTrue(hasattr(response, "message"))
        self.assertTrue(hasattr(response, "code"))
        self.assertTrue(hasattr(response, "request_id"))
        # test doesn't have body
        http_response = MockHttpResponse(status=508)
        response = BceResponse()
        old_len = len(response.__dict__)
        self.assertTrue(handler.parse_json(http_response, response))
        self.assertEqual(len(response.__dict__), old_len)


class TestBceHttpClient(TestClient):
    """test abort bce_http_client"""
    def test_parse_host_port(self):
        """test about parse host port"""
        # test default port for http
        endpoint = "1.2.3.4"
        default_protocol = baidubce.protocol.HTTP
        ret_protocol, host, port = utils.parse_host_port(endpoint, default_protocol)
        self.assertEqual(ret_protocol, baidubce.protocol.HTTP)
        self.assertEqual(host, endpoint)
        self.assertEqual(port, default_protocol.default_port)

        # test default port for https
        endpoint = "1.2.3.4"
        default_protocol = baidubce.protocol.HTTPS
        ret_protocol, host, port = utils.parse_host_port(endpoint, default_protocol)
        self.assertEqual(ret_protocol, baidubce.protocol.HTTPS)
        self.assertEqual(host, endpoint)
        self.assertEqual(port, default_protocol.default_port)

        # test specific port
        endpoint = "1.2.3.4:8080"
        default_protocol = baidubce.protocol.HTTP
        ret_protocol, host, port = utils.parse_host_port(endpoint, default_protocol)
        self.assertEqual(ret_protocol, baidubce.protocol.HTTP)
        self.assertEqual(host, "1.2.3.4")
        self.assertEqual(port, 8080)

        # test value error
        endpoint = "1.2.3.4:abcd"
        default_protocol = baidubce.protocol.HTTP
        self.assertRaises(ValueError, utils.parse_host_port, endpoint, default_protocol)

        # protocol unsupported
        endpoint = "ftp://1.2.3.4"
        default_protocol = baidubce.protocol.HTTP
        self.assertRaises(ValueError, utils.parse_host_port, endpoint, default_protocol)

        # test of endpoint dominates the protocol
        endpoint = "http://1.2.3.4:8080"
        default_protocol = baidubce.protocol.HTTPS
        ret_protocol, host, port = utils.parse_host_port(endpoint, default_protocol)
        self.assertEqual(ret_protocol, baidubce.protocol.HTTP)
        self.assertEqual(host, "1.2.3.4")
        self.assertEqual(port, 8080)

    def test_get_connection(self):
        """test abort get connection"""
        # test unknown protocol
        test_protocol = Expando({'name': 'unknown', 'default_port': 65535})
        host = "1.2.3.4"
        port = 8080
        connection_timeout = 1000
        self.assertRaises(ValueError,
                          bce_http_client._get_connection,
                          test_protocol, host, port, connection_timeout)

        # test http protocol
        test_protocol = protocol.HTTP
        host = "1.2.3.4"
        port = 8080
        connection_timeout = 1000
        conn = bce_http_client._get_connection(test_protocol, host, port, connection_timeout)
        self.assertEqual(conn.host, "1.2.3.4")
        self.assertEqual(conn.port, 8080)
        self.assertEqual(conn.timeout, 1)

        # test https protocol
        test_protocol = protocol.HTTPS
        host = "1.2.3.4"
        port = 8080
        connection_timeout = 1000
        conn = bce_http_client._get_connection(test_protocol, host, port, connection_timeout)

    def test_send_http_request_normal(self):
        """test abort send http request"""
        # test body is None
        conn = MockHttpConnection()
        method = http_methods.GET
        uri = "/unknown/unknown"
        headers = {"Content-Length": 15,
                   "Content-Encoding": "utf8",
                   }
        body = None
        send_buf_size = -1

        bce_http_client._send_http_request(conn,
                                           method,
                                           uri,
                                           headers,
                                           body,
                                           send_buf_size)
        self.assertEqual(conn.putrequest_called, 1)
        self.assertEqual(conn.putheader_called, 2)
        self.assertEqual(conn.endheaders_called, 1)
        self.assertEqual(conn.send_called, 0)
        self.assertEqual(conn.content, "")
        self.assertEqual(conn.getresponse_called, 1)

        # test body is string
        conn = MockHttpConnection()
        method = http_methods.GET
        uri = "/unknown/unknown"
        headers = {"Content-Length": 15,
                   "Content-Encoding": "utf8",
                   }
        body = "Test with string"
        send_buf_size = -1
        bce_http_client._send_http_request(conn,
                                           method,
                                           uri,
                                           headers,
                                           body,
                                           send_buf_size)
        self.assertEqual(conn.putrequest_called, 1)
        self.assertEqual(conn.putheader_called, 2)
        self.assertEqual(conn.endheaders_called, 1)
        self.assertEqual(conn.send_called, 1)
        self.assertEqual(conn.content, body)
        self.assertEqual(conn.getresponse_called, 1)

        # test body is input stream
        conn = MockHttpConnection()
        method = http_methods.GET
        uri = "/unknown/unknown"
        headers = {"Content-Length": 16,
                   "Content-Encoding": "utf8",
                   }
        body = MockInputStream("Test with string")
        send_buf_size = 5
        bce_http_client._send_http_request(conn,
                                           method,
                                           uri,
                                           headers,
                                           body,
                                           send_buf_size)
        self.assertEqual(conn.putrequest_called, 1)
        self.assertEqual(conn.putheader_called, 2)
        self.assertEqual(conn.endheaders_called, 1)
        self.assertEqual(conn.send_called, len("Test with string") / 5 + 1)
        self.assertEqual(conn.content, "Test with string")
        self.assertEqual(conn.getresponse_called, 1)

    def test_send_http_request_exception(self):
        """test abort send http request"""
        # test body is not sufficient
        conn = MockHttpConnection()
        method = http_methods.GET
        uri = "/unknown/unknown"
        headers = {"Content-Length": 100,
                   "Content-Encoding": "utf8",
                   }
        body = MockInputStream("Test with string")
        send_buf_size = 5
        self.assertRaises(BceClientError,
                          bce_http_client._send_http_request,
                          conn,
                          method,
                          uri,
                          headers,
                          body,
                          send_buf_size)
        self.assertEqual(conn.putrequest_called, 1)
        self.assertEqual(conn.putheader_called, 2)
        self.assertEqual(conn.endheaders_called, 1)
        self.assertEqual(conn.send_called, len("Test with string") / 5 + 1)

    def test_send_request(self):
        """test abort send request"""
        old_get_connection = bce_http_client._get_connection
        old_send_http_request = bce_http_client._send_http_request
        bce_http_client._get_connection = mock_get_connection
        handlers = [mock_handler_function_wrapper(True), mock_handler_function_wrapper(False)]
        uri = "/unknown/unknown"
        params = {"test": "test"}
        # test with socket exception
        body = None
        headers = None
        bce_http_client._send_http_request = mock_send_http_request_wrapper(True, None)
        bos_test_config.config.retry_policy = BackOffRetryPolicy()
        self.assertRaises(BceHttpClientError,
                          bce_http_client.send_request,
                          bos_test_config.config,
                          mock_sign_function,
                          handlers,
                          http_methods.GET,
                          uri,
                          body,
                          headers,
                          params)

        # test with value exception
        bos_test_config.config.retry_policy = NoRetryPolicy()
        body = 1
        headers = {"x-bce-date": "12345"}
        bce_http_client._send_http_request = mock_send_http_request_wrapper(False, None)
        self.assertRaises(ValueError,
                          bce_http_client.send_request,
                          bos_test_config.config,
                          mock_sign_function,
                          handlers,
                          http_methods.GET,
                          uri,
                          body,
                          headers,
                          params)

        # test others
        body = u"abcde"
        headers = {"x-bce-date": "12345"}
        params = None
        bce_http_client._send_http_request = mock_send_http_request_wrapper(False, {"err": "err"})
        response = bce_http_client.send_request(bos_test_config.config,
                                                mock_sign_function,
                                                handlers,
                                                http_methods.GET,
                                                uri,
                                                body,
                                                headers,
                                                params)
        self.assertEqual(response.metadata.err, "err")
        bce_http_client._get_connection = old_get_connection
        bce_http_client._send_http_request = old_send_http_request


class TestBceClientConfiguration(TestClient):
    """test BceClientConfiguration"""
    def test_init(self):
        """test BceClientConfiguration"""
        conf = BceClientConfiguration(1, 2, 3, 4, 5, 6, 7)
        self.assertEqual(conf.credentials, 1)
        self.assertEqual(conf.endpoint, 2)
        self.assertEqual(conf.protocol, 3)
        self.assertEqual(conf.region, 4)
        self.assertEqual(conf.connection_timeout_in_mills, 5)
        self.assertEqual(conf.send_buf_size, 6)
        self.assertEqual(conf.recv_buf_size, 7)


class TestGetRangeHeaderDict(TestClient):
    """test _get_range_header_dict"""
    def test_get_range_header_dict(self):
        """test _get_range_header_dict"""
        self.assertIsNone(bos_client.BosClient._get_range_header_dict(None))
        self.assertDictEqual(bos_client.BosClient._get_range_header_dict((0, 99)),
                          {"Range":'bytes=0-99'})
        err = None
        try:
            bos_client.BosClient._get_range_header_dict(123)
        except TypeError as e:
            err = e
        finally:
            self.assertIsNotNone(err)
        err = None
        try:
            bos_client.BosClient._get_range_header_dict([1, 2, 3])
        except ValueError as e:
            err = e
        finally:
            self.assertIsNotNone(err)


class MyClass(object):
    """
    wrapper class
    """
    @required(a=int, b=list, c=(str, unicode))
    def my_func(self, a, b, c, d=None):
        """
        sample function to use required
        :param a:
        :param b:
        :param c:
        :param d:
        :return:
        """
        return True


class TestDecorator(TestClient):
    """test required decorator"""
    def positive_test(self):
        """
        test of positive cases
        :return:
        """
        self.assertTrue(MyClass().my_func(1, [], "a"))
        self.assertTrue(MyClass().my_func(1, [], u"a", 123))
        self.assertTrue(MyClass().my_func(1, [], "a", "abc"))
        self.assertTrue(MyClass().my_func(1, [], u"a", None))
        self.assertTrue(MyClass().my_func(a=1, b=[], c="a"))
        self.assertTrue(MyClass().my_func(a=1, b=[], c=u"a", d=123))
        self.assertTrue(MyClass().my_func(a=1, b=[], c="a", d="abc"))
        self.assertTrue(MyClass().my_func(a=1, b=[], c=u"a", d=None))

    def negative_test_value(self):
        """
        test of negative cases
        :return:
        """
        self.assertRaises(ValueError, MyClass().my_func, None, [], "a")
        self.assertRaises(ValueError, MyClass().my_func, 1, None, "a")
        self.assertRaises(ValueError, MyClass().my_func, 1, [], None)
        self.assertRaises(ValueError, MyClass().my_func, a=None, b=[], c="a")
        self.assertRaises(ValueError, MyClass().my_func, a=1, b=None, c="a")
        self.assertRaises(ValueError, MyClass().my_func, a=1, b=[], c=None)

    def negative_test_type(self):
        """
        test of negative cases
        :return:
        """
        self.assertRaises(TypeError, MyClass().my_func, "a", [], "a")
        self.assertRaises(TypeError, MyClass().my_func, 1, 1, "a")
        self.assertRaises(TypeError, MyClass().my_func, 1, [], [])
        self.assertRaises(TypeError, MyClass().my_func, a="a", b=[], c="a")
        self.assertRaises(TypeError, MyClass().my_func, a=1, b=1, c="a")
        self.assertRaises(TypeError, MyClass().my_func, a=1, b=[], c=[])


def run_test():
    """start run test"""
    runner = unittest.TextTestRunner()
    runner.run(unittest.makeSuite(TestClient))
    runner.run(unittest.makeSuite(TestCopyObject))
    runner.run(unittest.makeSuite(TestGeneratePreSignedUrl))
    runner.run(unittest.makeSuite(TestListMultipartsUploads))
    runner.run(unittest.makeSuite(TestSetBucketAcl))
    runner.run(unittest.makeSuite(TestGetBucketAcl))
    runner.run(unittest.makeSuite(TestGetObjectMetaData))
    runner.run(unittest.makeSuite(TestGetObject))
    runner.run(unittest.makeSuite(TestListBuckets))
    runner.run(unittest.makeSuite(TestListObjects))
    runner.run(unittest.makeSuite(TestListParts))
    runner.run(unittest.makeSuite(TestPutObject))
    runner.run(unittest.makeSuite(TestMultiUploadFile))
    runner.run(unittest.makeSuite(TestAuthorization))
    runner.run(unittest.makeSuite(TestAbortMultipartUpload))
    runner.run(unittest.makeSuite(TestUtil))
    runner.run(unittest.makeSuite(TestHandler))
    runner.run(unittest.makeSuite(TestBceHttpClient))
    runner.run(unittest.makeSuite(TestDoesBucketExist))
    runner.run(unittest.makeSuite(TestBceClientConfiguration))
    runner.run(unittest.makeSuite(TestGetRangeHeaderDict))
    runner.run(unittest.makeSuite(TestDecorator))

run_test()
cov.stop()
cov.save()
cov.html_report()

